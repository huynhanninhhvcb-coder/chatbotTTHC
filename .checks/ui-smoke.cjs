const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');

const BASE_URL = process.env.UI_BASE_URL || 'http://127.0.0.1:5000';
const DEBUG_PORT = 9225;
const CHECKS_DIR = __dirname;
const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const PROFILE_DIR = path.join(CHECKS_DIR, 'chrome-profile-redesign');
const results = [];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

class CDP {
    constructor(socket) {
        this.socket = socket;
        this.nextId = 0;
        this.pending = new Map();
        this.handlers = new Map();
        socket.addEventListener('message', event => {
            const message = JSON.parse(event.data);
            if (message.id) {
                const request = this.pending.get(message.id);
                if (!request) return;
                this.pending.delete(message.id);
                clearTimeout(request.timer);
                if (message.error) request.reject(new Error(JSON.stringify(message.error)));
                else request.resolve(message.result);
            } else {
                for (const handler of this.handlers.get(message.method) || []) handler(message.params);
            }
        });
    }
    static async connect(url) {
        const socket = new WebSocket(url);
        await new Promise((resolve, reject) => {
            socket.addEventListener('open', resolve, { once: true });
            socket.addEventListener('error', reject, { once: true });
        });
        return new CDP(socket);
    }
    send(method, params = {}) {
        const id = ++this.nextId;
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => {
                this.pending.delete(id);
                reject(new Error(`CDP timeout: ${method}`));
            }, 15000);
            this.pending.set(id, { resolve, reject, timer });
            this.socket.send(JSON.stringify({ id, method, params }));
        });
    }
    on(method, handler) {
        if (!this.handlers.has(method)) this.handlers.set(method, []);
        this.handlers.get(method).push(handler);
    }
    async evaluate(expression) {
        const result = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
        if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
        return result.result.value;
    }
    close() { this.socket.close(); }
}

async function poll(fn, description, timeout = 10000) {
    const deadline = Date.now() + timeout;
    let lastError;
    while (Date.now() < deadline) {
        try { const value = await fn(); if (value) return value; } catch (error) { lastError = error; }
        await sleep(100);
    }
    throw new Error(`Timed out: ${description}${lastError ? ` (${lastError.message})` : ''}`);
}

function record(name, detail) {
    results.push({ name, ok: true, detail });
    console.log(`PASS ${name}${detail ? ` ${JSON.stringify(detail)}` : ''}`);
}

const MOCK_RECOGNITION = `(() => {
    const calls = { starts: 0, stops: 0, aborts: 0, spoken: [], requests: [] };
    class MockRecognition {
        constructor() { window.__recognition = this; }
        start() { calls.starts++; this.onstart?.({}); }
        stop() { calls.stops++; this.onend?.({}); }
        abort() { calls.aborts++; this.onend?.({}); }
    }
    Object.defineProperty(window, 'SpeechRecognition', { configurable: true, value: MockRecognition });
    Object.defineProperty(window, 'webkitSpeechRecognition', { configurable: true, value: MockRecognition });
    Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
        cancel() {}, getVoices() { return []; },
        speak(utterance) { calls.spoken.push(utterance.text); utterance.onstart?.({}); setTimeout(() => utterance.onend?.({}), 20); }
    }});
    const realFetch = window.fetch.bind(window);
    window.fetch = (input, options) => {
        if (String(input).endsWith('/chat')) {
            calls.requests.push(JSON.parse(options.body));
            return new Promise(resolve => setTimeout(() => resolve(new Response(window.__mockReply || 'Đã nhận câu hỏi bằng giọng nói.', {status:200, headers:{'Content-Type':'text/plain; charset=utf-8', 'X-Context':'append'}})), window.__mockDelay ?? 1100));
        }
        return realFetch(input, options);
    };
    window.__speechMock = {
        calls,
        result(text, final) { const result = [{transcript:text, confidence:1}]; result.isFinal = final; window.__recognition.onresult?.({resultIndex:0, results:[result]}); },
        end() { window.__recognition.onend?.({}); },
        error(error) { window.__recognition.onerror?.({error}); }
    };
})();`;

async function createPage({ width = 390, height = 844, mockSpeech = false, unsupported = false, reducedMotion = true } = {}) {
    const target = await (await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/new?about:blank`, { method: 'PUT' })).json();
    const cdp = await CDP.connect(target.webSocketDebuggerUrl);
    cdp.targetId = target.id;
    cdp.errors = [];
    cdp.on('Runtime.exceptionThrown', event => cdp.errors.push(event.exceptionDetails));
    await cdp.send('Runtime.enable');
    await cdp.send('Page.enable');
    await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width < 768 });
    await cdp.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: reducedMotion ? 'reduce' : 'no-preference' }] });
    cdp.on('Fetch.requestPaused', event => {
        const local = event.request.url.startsWith(BASE_URL) || event.request.url.startsWith('data:') || event.request.url.startsWith('about:');
        cdp.send(local ? 'Fetch.continueRequest' : 'Fetch.failRequest', local ? { requestId: event.requestId } : { requestId: event.requestId, errorReason: 'BlockedByClient' }).catch(() => {});
    });
    await cdp.send('Fetch.enable', { patterns: [{ urlPattern: '*' }] });
    await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: `window.__motionEvents=[];for(const type of ['animationstart','animationend','animationcancel'])document.addEventListener(type,event=>window.__motionEvents.push({type,name:event.animationName,target:event.target.id||String(event.target.className),pseudo:event.pseudoElement}));` });
    if (mockSpeech) await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: MOCK_RECOGNITION });
    if (unsupported) await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: `Object.defineProperty(window,'SpeechRecognition',{configurable:true,value:undefined});Object.defineProperty(window,'webkitSpeechRecognition',{configurable:true,value:undefined});` });
    await cdp.send('Page.navigate', { url: BASE_URL });
    await poll(() => cdp.evaluate(`document.readyState === 'complete' && Boolean(document.querySelector('#chatMessages .message')) && Array.from(document.querySelectorAll('img.brand-logo,.bot-avatar img')).every(img=>img.complete&&img.naturalWidth>0)`), 'page and uploaded logo initialization');
    await sleep(250);
    return cdp;
}

async function closePage(cdp) {
    await cdp.send('Page.close').catch(() => {});
    cdp.close();
}

async function capture(cdp, filename) {
    const result = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    fs.writeFileSync(path.join(CHECKS_DIR, filename), Buffer.from(result.data, 'base64'));
}

async function geometry(cdp) {
    return cdp.evaluate(`(() => {
        const selectors = ['#micBtn','#textInput','#sendTextBtn','#bhytBtn','#mauDonBtn','#khuPhoBtn','#menuBtn'];
        const rect = el => {const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom};};
        const controls = selectors.map(selector => {
            const el = document.querySelector(selector);
            if (!el) return {selector, missing:true};
            const r = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            const visible = Boolean(el.getClientRects().length) && style.visibility !== 'hidden';
            return {selector, visible, x:r.x, y:r.y, width:r.width, height:r.height, right:r.right, bottom:r.bottom, background:style.backgroundColor, backgroundImage:style.backgroundImage, color:style.color};
        });
        const messages=document.getElementById('chatMessages');
        const firstMessage=messages.querySelector('.message');
        const avatar=firstMessage?.querySelector('.avatar');
        const content=firstMessage?.querySelector('.message-content');
        const composer=document.querySelector('.composer');
        const logo=document.querySelector('img.brand-logo');
        const botLogo=document.querySelector('.bot-avatar img');
        return {width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,bodyScrollWidth:document.body.scrollWidth,
            shell:rect(document.querySelector('.app-shell')),messages:rect(messages),composer:composer?rect(composer):null,
            welcomeScrollTop:messages.scrollTop,firstMessageTop:firstMessage?.getBoundingClientRect().top,avatarTop:avatar?.getBoundingClientRect().top,contentTop:content?.getBoundingClientRect().top,messagesTop:messages.getBoundingClientRect().top,controls,
            logo:logo?{src:logo.getAttribute('src'),naturalWidth:logo.naturalWidth,naturalHeight:logo.naturalHeight,...rect(logo)}:null,
            avatarLogo:botLogo?{src:botLogo.getAttribute('src'),naturalWidth:botLogo.naturalWidth,naturalHeight:botLogo.naturalHeight}:null,
            obsoleteCount:document.querySelectorAll('.hero,.voice-dock,.bottom-nav,[role="tabpanel"]').length};
    })()`);
}

async function verifyLayout(width, height, screenshot) {
    const cdp = await createPage({ width, height });
    try {
        const layout = await geometry(cdp);
        assert(layout.scrollWidth <= layout.width + 1, `HTML horizontal overflow ${JSON.stringify(layout)}`);
        assert(layout.bodyScrollWidth <= layout.width + 1, `Body horizontal overflow ${JSON.stringify(layout)}`);
        assert(layout.welcomeScrollTop <= 1, `Initial welcome should start at top: ${JSON.stringify(layout)}`);
        assert(layout.firstMessageTop >= layout.messagesTop - 1, `Initial welcome opening should be visible: ${JSON.stringify(layout)}`);
        assert(layout.avatarTop >= layout.messagesTop - 1, `Initial welcome avatar should be visible: ${JSON.stringify(layout)}`);
        assert(layout.contentTop >= layout.messagesTop - 1, `Initial welcome content should be visible: ${JSON.stringify(layout)}`);
        for (const control of layout.controls) {
            assert(!control.missing, `Missing ${control.selector}`);
            assert(control.visible, `Hidden ${control.selector}`);
            assert(control.x >= -1 && control.y >= -1 && control.right <= width + 1 && control.bottom <= height + 1, `Control outside viewport ${JSON.stringify(control)}`);
        }
        const mic = layout.controls.find(control => control.selector === '#micBtn');
        const send = layout.controls.find(control => control.selector === '#sendTextBtn');
        const minimumMicSize = height <= 420 ? 36 : 40;
        assert(mic.width >= minimumMicSize && mic.height >= minimumMicSize && mic.width <= 48 && mic.height <= 48, `Microphone should remain a compact usable target: ${JSON.stringify(mic)}`);
        assert(mic.width >= send.width && mic.height >= send.height, 'Voice input should be at least as prominent as the send control');
        const rgb = value => value.match(/rgba?\(([^)]+)\)/)?.[1].match(/[\d.]+/g)?.slice(0,3).map(Number) || [];
        const micPaint = mic.backgroundImage !== 'none' ? mic.backgroundImage : mic.background;
        const micBackground = rgb(micPaint);
        const micColor = rgb(mic.color);
        const sendBackground = rgb(send.background);
        assert(micBackground.length===3 && micBackground[0]>180 && micBackground[1]<110 && micBackground[2]<110, `Primary microphone should have a red background: ${micPaint}`);
        assert(micColor.length===3 && micColor.every(channel=>channel>=240), `Microphone icon should remain white: ${mic.color}`);
        assert(sendBackground.length===3 && Math.max(...sendBackground)-Math.min(...sendBackground)<=15 && Math.min(...sendBackground)>=200, `Secondary send control should have a neutral background: ${send.background}`);
        assert(layout.composer && layout.composer.height <= 100, `Resting composer takes too much chat space: ${JSON.stringify(layout.composer)}`);
        const minimumChatFraction = height <= 420 ? 0.40 : width <= 320 ? 0.55 : 0.60;
        assert(layout.messages.height / layout.shell.height >= minimumChatFraction, `Chat should use at least ${minimumChatFraction * 100}% of the app: ${JSON.stringify(layout)}`);
        assert.equal(layout.obsoleteCount, 0, 'Removed hero, microphone dock and navigation panels should not remain');
        assert(layout.logo && layout.logo.src.includes('logo-minh-phung.png') && layout.logo.naturalWidth > 0, 'Header should load the uploaded full logo');
        assert(layout.avatarLogo && layout.avatarLogo.src === layout.logo.src && layout.avatarLogo.naturalWidth > 0, 'Bot avatar should use the same uploaded logo asset');
        assert.equal(cdp.errors.length, 0, `Browser JS errors: ${JSON.stringify(cdp.errors)}`);
        if (screenshot) await capture(cdp, screenshot);
        record(`layout ${width}x${height}`, {mic:[mic.width,mic.height],send:[send.width,send.height],micBackground:micPaint,micColor:mic.color,sendBackground:send.background,composerHeight:layout.composer.height,chatFraction:Number((layout.messages.height/layout.shell.height).toFixed(2)),logoSize:[layout.logo.naturalWidth,layout.logo.naturalHeight]});
    } finally { await closePage(cdp); }
}

async function click(cdp, id) {
    await cdp.evaluate(`document.getElementById(${JSON.stringify(id)}).click()`);
}

async function menuState(cdp) {
    return cdp.evaluate(`({expanded:document.getElementById('menuBtn').getAttribute('aria-expanded'),hidden:document.getElementById('chatMenu').hidden,visible:Boolean(document.getElementById('chatMenu').getClientRects().length)})`);
}

async function openMenu(cdp) {
    if ((await menuState(cdp)).hidden) await click(cdp, 'menuBtn');
    const state = await menuState(cdp);
    assert.equal(state.expanded, 'true', `Menu should expose expanded state: ${JSON.stringify(state)}`);
    assert.equal(state.hidden, false, `Menu should open: ${JSON.stringify(state)}`);
}

async function verifyInteraction() {
    const cdp = await createPage();
    try {
        assert.equal((await menuState(cdp)).hidden, true, 'Menu should start closed');
        await openMenu(cdp);
        await cdp.send('Input.dispatchKeyEvent', { type:'keyDown', key:'Escape', code:'Escape' });
        await cdp.send('Input.dispatchKeyEvent', { type:'keyUp', key:'Escape', code:'Escape' });
        assert.equal((await menuState(cdp)).hidden, true, 'Escape should dismiss menu');
        await openMenu(cdp);
        await cdp.evaluate(`document.getElementById('chatMessages').click()`);
        assert.equal((await menuState(cdp)).hidden, true, 'Outside click should dismiss menu');
        await openMenu(cdp);
        await click(cdp, 'menuBtn');
        assert.equal((await menuState(cdp)).hidden, true, 'Menu button should toggle closed');
        record('menu toggle, Escape and outside dismissal');

        await openMenu(cdp);
        const audioBefore = await cdp.evaluate(`document.getElementById('audioToggleBtn').getAttribute('aria-pressed')`);
        await click(cdp, 'audioToggleBtn');
        const audioAfter = await cdp.evaluate(`document.getElementById('audioToggleBtn').getAttribute('aria-pressed')`);
        assert.notEqual(audioBefore, audioAfter, 'Audio preference should toggle');
        await openMenu(cdp);
        await click(cdp, 'audioToggleBtn');
        assert.equal(await cdp.evaluate(`document.getElementById('audioToggleBtn').getAttribute('aria-pressed')`), audioBefore, 'Audio preference should toggle back');
        record('audio preference in compact menu');

        await openMenu(cdp);
        await click(cdp, 'voiceGuideBtn');
        assert.equal(await cdp.evaluate(`document.activeElement.id`), 'micBtn', 'Voice guidance should focus the microphone');
        assert(/micro/i.test(await cdp.evaluate(`document.body.innerText`)), 'Voice guidance should be visible');
        record('voice guidance from menu');

        await cdp.evaluate(`window.__opened=[];window.open=(...args)=>{window.__opened.push(args);return null;}`);
        for (const id of ['bhytBtn','mauDonBtn','khuPhoBtn']) await click(cdp, id);
        const opened = await cdp.evaluate(`window.__opened.map(args=>({url:args[0],target:args[1],features:args[2]}))`);
        assert.equal(opened.length, 3, 'Three shortcuts should open destinations');
        assert(opened[0].url.includes('baohiemxahoi.gov.vn') && opened[1].url === '/mau-don' && opened[2].url.includes('phuongminhphung'), `Shortcut destinations changed: ${JSON.stringify(opened)}`);
        assert(opened.every(item=>item.target==='_blank' && item.features.includes('noopener')), 'Shortcut destinations should open safely in a new tab');
        record('BHYT, form and neighborhood shortcuts');

        await cdp.evaluate(`document.getElementById('textInput').value='hello';document.getElementById('textInput').focus()`);
        await cdp.send('Input.dispatchKeyEvent', { type:'keyDown', key:'Enter', code:'Enter' });
        await cdp.send('Input.dispatchKeyEvent', { type:'keyUp', key:'Enter', code:'Enter' });
        await poll(() => cdp.evaluate(`document.querySelectorAll('#chatMessages .message.user').length===1 && document.querySelectorAll('#chatMessages .message.bot').length>=2 && !document.getElementById('typingIndicator') && document.getElementById('chatMessages').textContent.includes('Xin chào! Tôi có thể')`), 'real greeting response');
        record('text Enter greeting to real Flask /chat');
        await openMenu(cdp);
        await click(cdp, 'clearBtn');
        await poll(() => cdp.evaluate(`document.querySelectorAll('#chatMessages .message.user').length===0 && document.querySelectorAll('#chatMessages .message.bot').length===1 && document.getElementById('clearBtn').getAttribute('aria-busy')!=='true'`), 'cleared welcome');
        assert.equal((await menuState(cdp)).hidden, true, 'Clear should close the menu');
        record('clear restores welcome and closes menu');
        assert.equal(cdp.errors.length, 0, `Browser JS errors: ${JSON.stringify(cdp.errors)}`);
    } finally { await closePage(cdp); }
}

async function voiceState(cdp) {
    return cdp.evaluate(`(() => {
        const mic=document.getElementById('micBtn');
        return {pressed:mic.getAttribute('aria-pressed'), label:mic.getAttribute('aria-label'), listening:mic.classList.contains('listening'), disabled:mic.disabled, status:document.getElementById('statusText').textContent,
            text:document.getElementById('textInput').value, visibleText:document.body.innerText, users:document.querySelectorAll('#chatMessages .message.user').length,
            requests:window.__speechMock?.calls.requests.length, starts:window.__speechMock?.calls.starts};
    })()`);
}

async function verifyVoice() {
    const cdp = await createPage({ mockSpeech: true });
    try {
        await cdp.evaluate(`document.getElementById('micBtn').click()`);
        let state = await voiceState(cdp);
        assert(state.listening && state.pressed === 'true', `Listening state missing: ${JSON.stringify(state)}`);
        assert(/dừng/i.test(state.label), `Listening label should offer stop: ${JSON.stringify(state)}`);
        record('voice start and accessible listening state');
        await cdp.evaluate(`window.__speechMock.result('Xin chào phường',false)`);
        state = await voiceState(cdp);
        assert.equal(state.users, 0, 'Interim recognition must not submit');
        assert(state.text.includes('Xin chào phường') || state.visibleText.includes('Xin chào phường'), 'Interim transcript must be visible');
        record('interim transcript without submission');
        await capture(cdp,'listening-mobile.png');
        await cdp.evaluate(`window.__speechMock.result('Xin chào phường Minh Phụng',true);window.__speechMock.end()`);
        state = await voiceState(cdp);
        assert.equal(state.users, 1, 'Final transcript submits one question');
        assert.equal(state.requests, 1, 'Final transcript requests one reply');
        assert(!state.listening && state.pressed === 'false', `Final transcript should stop listening: ${JSON.stringify(state)}`);
        assert(!/sẵn sàng/i.test(state.status), `onend overwrote processing state: ${JSON.stringify(state)}`);
        record('final transcript submits and onend preserves processing');
        await poll(() => cdp.evaluate(`document.getElementById('chatMessages').textContent.includes('Đã nhận câu hỏi bằng giọng nói.') && !document.getElementById('typingIndicator')`), 'mocked voice reply');
        await sleep(100);
        await cdp.evaluate(`document.getElementById('micBtn').click();window.__speechMock.error('no-speech');window.__speechMock.end()`);
        state = await voiceState(cdp);
        assert(/không.*(nghe|giọng nói)|chưa.*(nghe|giọng nói)/i.test(state.status + state.visibleText), `No-speech feedback missing: ${JSON.stringify(state)}`);
        assert(!state.listening && state.pressed === 'false', 'Error should reset listening state');
        record('no-speech feedback survives onend');
        await cdp.evaluate(`document.getElementById('micBtn').click();window.__speechMock.error('not-allowed');window.__speechMock.end()`);
        state = await voiceState(cdp);
        assert(/quyền.*micro|micro.*quyền|cho phép.*micro/i.test(state.status + state.visibleText), `Permission feedback missing: ${JSON.stringify(state)}`);
        assert(!state.listening && state.pressed === 'false', 'Permission error should reset listening state');
        record('microphone permission feedback survives onend');
        assert.equal(cdp.errors.length, 0, `Browser JS errors: ${JSON.stringify(cdp.errors)}`);
    } finally { await closePage(cdp); }
    const unsupported = await createPage({ unsupported: true });
    try {
        let state = await voiceState(unsupported);
        assert(!state.disabled, 'Unsupported browser should keep microphone available for guidance');
        await unsupported.evaluate(`document.getElementById('micBtn').click()`);
        state = await voiceState(unsupported);
        assert(/chrome|edge/i.test(state.visibleText), 'Microphone click should expose browser fallback guidance');
        await openMenu(unsupported);
        await click(unsupported, 'clearBtn');
        await poll(() => unsupported.evaluate(`document.getElementById('clearBtn').getAttribute('aria-busy')!=='true'`), 'unsupported conversation cleared');
        state = await voiceState(unsupported);
        assert(!state.disabled, 'Clear should preserve available unsupported microphone');
        assert(/không hỗ trợ|chrome|edge|có thể nhập/i.test(state.status+state.visibleText), 'Clear should preserve unsupported browser fallback feedback');
        record('unsupported voice browser fallback hint');
    } finally { await closePage(unsupported); }
}

async function verifyLongConversation() {
    const cdp = await createPage({mockSpeech:true});
    try {
        const reply = '**Nội dung trao đổi**\n\nTôi đã nhận câu hỏi của bạn. Dưới đây là các mục bạn có thể trao đổi thêm với trợ lý.\n\n**Thông tin cần chuẩn bị**\n1. Tên thủ tục bạn muốn tra cứu\n2. Nội dung bạn cần được hướng dẫn\n3. Các câu hỏi liên quan đến hồ sơ\n\n**Hướng dẫn tiếp theo**\nBạn có thể tiếp tục nhập câu hỏi hoặc sử dụng micro để trao đổi. Thông tin trong màn hình này là nội dung mô phỏng để kiểm tra cách hiển thị câu trả lời dài.\n\nBạn cần hỗ trợ thêm nội dung nào?';
        await cdp.evaluate(`window.__mockReply=${JSON.stringify(reply)};window.__mockDelay=10;document.getElementById('textInput').value='Tôi cần hỗ trợ tra cứu thủ tục.';document.getElementById('textInput').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('sendTextBtn').click()`);
        await poll(() => cdp.evaluate(`document.querySelectorAll('#chatMessages .message.user').length===1 && !document.getElementById('typingIndicator') && document.querySelectorAll('.number-badge').length===3`), 'long formatted reply');
        const state = await cdp.evaluate(`(() => {const messages=document.getElementById('chatMessages'),composer=document.querySelector('.composer'),last=messages.lastElementChild;return {scrollable:messages.scrollHeight>messages.clientHeight,atLatest:Math.abs(messages.scrollHeight-messages.clientHeight-messages.scrollTop)<2,lastBottom:last.getBoundingClientRect().bottom,composerTop:composer.getBoundingClientRect().top,overflow:document.documentElement.scrollWidth>innerWidth,micWidth:document.getElementById('micBtn').getBoundingClientRect().width,headings:messages.querySelectorAll('.answer-section-title').length};})()`);
        assert(state.scrollable && state.atLatest, `Long answer should scroll within chat: ${JSON.stringify(state)}`);
        assert(state.lastBottom <= state.composerTop + 1, 'Composer must not cover the final answer');
        assert(!state.overflow && state.micWidth <= 48, 'Long answer should preserve compact controls and page width');
        assert.equal(state.headings,3,'Formatted answer should render its three heading cards');
        await capture(cdp, 'conversation-mobile.png');
        record('long formatted reply scrolls above compact composer', state);
    } finally {await closePage(cdp);}
}

async function verifySuggestions() {
    const cdp = await createPage({mockSpeech:true});
    try {
        const reply = 'Trả lời mẫu có gợi ý.' + '\x1e' + JSON.stringify(['Lệ phí bao nhiêu?', 'Nộp ở đâu?']);
        await cdp.evaluate(`window.__mockReply=${JSON.stringify(reply)};window.__mockDelay=10;document.getElementById('textInput').value='Thủ tục khai sinh';document.getElementById('textInput').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('sendTextBtn').click()`);
        await poll(() => cdp.evaluate(`[...document.querySelectorAll('#chatMessages .chip-btn')].filter(b=>!b.disabled).length===2`), 'suggestion chips enabled');
        let state = await cdp.evaluate(`(() => {const bot=[...document.querySelectorAll('#chatMessages .message.bot')].pop();return {text:bot.querySelector('.message-content').textContent,chips:[...bot.querySelectorAll('.chip-btn')].map(b=>b.textContent),chipsBeforeTime:bot.querySelector('.quick-replies').nextElementSibling?.className};})()`);
        assert(state.text.includes('Trả lời mẫu có gợi ý.') && !state.text.includes('\x1e') && !state.text.includes('Lệ phí'), `Suggestions must not leak into answer text: ${JSON.stringify(state)}`);
        assert.deepEqual(state.chips, ['Lệ phí bao nhiêu?', 'Nộp ở đâu?']);
        assert.equal(state.chipsBeforeTime, 'timestamp', 'Chips should sit between answer and timestamp');
        await capture(cdp, 'suggestions-mobile.png');
        await cdp.evaluate(`window.__mockReply='Lệ phí mẫu.';document.querySelector('#chatMessages .chip-btn').click()`);
        await poll(() => cdp.evaluate(`window.__speechMock.calls.requests.length===2 && document.querySelectorAll('#chatMessages .message.user').length===2 && !document.getElementById('typingIndicator')`), 'suggestion chip sends question');
        state = await cdp.evaluate(`window.__speechMock.calls.requests[1]`);
        assert.equal(state.message, 'Lệ phí bao nhiêu?', 'Chip should send its own text');
        assert.deepEqual(state.history, [{role:'user',content:'Thủ tục khai sinh'},{role:'assistant',content:'Trả lời mẫu có gợi ý.'}], 'Follow-up should carry history without suggestions');
        record('suggestion chips render after answer and send follow-up with history');
    } finally {await closePage(cdp);}
}

async function verifyAnimations() {
    const cdp = await createPage({mockSpeech:true,reducedMotion:false});
    try {
        const entranceNames = ['headerEnter','logoEnter','serviceEnter','composerEnter','messageFromLeft','avatarHello'];
        await poll(() => cdp.evaluate(`(${JSON.stringify(entranceNames)}).every(name=>window.__motionEvents.some(event=>event.type==='animationstart'&&event.name===name))`), 'entrance animations start');
        await poll(() => cdp.evaluate(`Array.from(document.querySelectorAll('.app-header,.brand-logo,.service-button,.composer,#chatMessages .message,.bot-avatar')).every(el=>el.getAnimations().every(animation=>animation.playState==='finished'))`), 'entrance animations settle');
        const before = await geometry(cdp);
        assert.equal(await cdp.evaluate(`document.querySelector('.app-shell').dataset.state`),'ready','Ready state should drive the microphone invitation');
        assert.equal(await cdp.evaluate(`getComputedStyle(document.getElementById('micBtn'),'::before').animationName`),'voiceInvite','Ready microphone should invite voice input');
        record('normal motion entrance settles with visible voice invitation',entranceNames);

        await openMenu(cdp);
        await poll(() => cdp.evaluate(`window.__motionEvents.some(event=>event.type==='animationstart'&&event.name==='menuEnter')`), 'menu entrance animation');
        await click(cdp,'menuBtn');
        assert.equal((await menuState(cdp)).hidden,true,'Animated menu should close');
        assert.equal(await cdp.evaluate(`getComputedStyle(document.getElementById('chatMenu')).animationName`),'none','Closed menu should stop animating');
        const send = await cdp.evaluate(`(() => {window.__mockReply='Đã nhận câu hỏi của bạn.';document.getElementById('textInput').value='Kiểm tra gửi tin nhắn';document.getElementById('textInput').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('sendTextBtn').click();return {active:document.getElementById('sendTextBtn').classList.contains('is-sending'),name:getComputedStyle(document.querySelector('#sendTextBtn svg')).animationName};})()`);
        assert(send.active&&send.name==='sendFly',`Send feedback should animate immediately: ${JSON.stringify(send)}`);
        await poll(() => cdp.evaluate(`window.__motionEvents.some(event=>event.type==='animationstart'&&event.name==='sendFly') && window.__motionEvents.some(event=>event.type==='animationstart'&&event.name==='messageFromRight')`), 'send and user message animations');
        await poll(() => cdp.evaluate(`!document.getElementById('sendTextBtn').classList.contains('is-sending')`), 'one-shot send class cleanup');
        await poll(() => cdp.evaluate(`!document.getElementById('typingIndicator') && document.querySelectorAll('#chatMessages .message.bot').length===2 && document.querySelector('.app-shell').dataset.state==='ready'`), 'animated bot response completes');
        await click(cdp,'micBtn');
        const listening = await cdp.evaluate(`({shell:document.querySelector('.app-shell').classList.contains('listening'),state:document.querySelector('.app-shell').dataset.state,mic:document.getElementById('micBtn').classList.contains('listening'),animation:getComputedStyle(document.getElementById('micBtn')).animationName,wave:getComputedStyle(document.querySelector('.mic-waveform span')).animationName})`);
        assert(listening.shell&&listening.mic&&listening.state==='listening'&&listening.animation==='micListening'&&listening.wave==='wave',`Listening motion should follow microphone state: ${JSON.stringify(listening)}`);
        await capture(cdp,'listening-animated-mobile.png');
        await cdp.evaluate(`window.__speechMock.error('no-speech');window.__speechMock.end()`);
        assert.equal(await cdp.evaluate(`document.querySelector('.app-shell').classList.contains('listening')||document.getElementById('micBtn').classList.contains('listening')`),false,'Listening classes should clear after recognition ends');
        assert.equal(await cdp.evaluate(`getComputedStyle(document.getElementById('micBtn')).animationName`),'none','Microphone pulse should stop after recognition ends');
        await poll(() => cdp.evaluate(`Array.from(document.querySelectorAll('#chatMessages .message,.bot-avatar')).every(el=>el.getAnimations().every(animation=>animation.playState==='finished'))`), 'message motion settles');
        const after = await geometry(cdp);
        assert(Math.abs(after.composer.height-before.composer.height)<1,'Animations should preserve resting composer height');
        assert(Math.abs(after.messages.height-before.messages.height)<1,'Animations should preserve chat space');
        assert(after.scrollWidth<=after.width+1 && after.bodyScrollWidth<=after.width+1,'Animated interactions should not cause horizontal overflow');
        for(const control of after.controls)assert(control.x>=-1&&control.y>=-1&&control.right<=after.width+1&&control.bottom<=after.height+1,`Animated control outside viewport: ${JSON.stringify(control)}`);
        assert.equal(cdp.errors.length,0,`Browser JS errors: ${JSON.stringify(cdp.errors)}`);
        record('animated menu, messages, send and microphone clean up without moving controls',{composerHeight:after.composer.height,chatHeight:after.messages.height});
    } finally {await closePage(cdp);}

    const reduced = await createPage({mockSpeech:true,reducedMotion:true});
    try {
        const assertReduced = async () => {
            const state = await reduced.evaluate(`(() => {const selectors=['.app-header','.brand-logo','.service-button','.composer','#chatMenu','#chatMessages .message','#micBtn','.mic-waveform span','#sendTextBtn svg'];return {styles:selectors.map(selector=>{const el=document.querySelector(selector),style=getComputedStyle(el);return {selector,animation:style.animationName,transition:style.transitionDuration};}),pseudo:getComputedStyle(document.getElementById('micBtn'),'::before').animationName,scroll:getComputedStyle(document.getElementById('chatMessages')).scrollBehavior,running:document.getAnimations().filter(animation=>animation.playState==='running'||animation.pending).length};})()`);
            for(const style of state.styles){assert.equal(style.animation,'none',`Reduced motion animation remains: ${JSON.stringify(style)}`);assert(style.transition.split(',').every(value=>parseFloat(value)===0),`Reduced motion transition remains: ${JSON.stringify(style)}`);}
            assert.equal(state.pseudo,'none','Reduced motion should stop the microphone invitation halo');
            assert.equal(state.scroll,'auto','Reduced motion should disable smooth scrolling');
            assert.equal(state.running,0,'Reduced motion should leave no running visual animations');
        };
        await assertReduced();
        await openMenu(reduced);
        await assertReduced();
        await click(reduced,'menuBtn');
        await click(reduced,'micBtn');
        assert.equal((await voiceState(reduced)).listening,true,'Reduced motion should preserve listening feedback');
        await assertReduced();
        await reduced.evaluate(`window.__speechMock.error('no-speech');window.__speechMock.end();window.__mockDelay=10;document.getElementById('textInput').value='Kiểm tra giảm chuyển động';document.getElementById('textInput').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('sendTextBtn').click()`);
        assert.equal(await reduced.evaluate(`document.getElementById('sendTextBtn').classList.contains('is-sending')`),false,'Reduced motion should not leave a transient send class');
        await poll(() => reduced.evaluate(`!document.getElementById('typingIndicator')&&document.querySelectorAll('#chatMessages .message.bot').length===2`), 'reduced-motion response');
        await assertReduced();
        assert.equal(reduced.errors.length,0,`Browser JS errors: ${JSON.stringify(reduced.errors)}`);
        record('reduced motion disables animation and smooth scrolling while controls still work');
    } finally {await closePage(reduced);}
}

async function main() {
    fs.mkdirSync(CHECKS_DIR, { recursive: true });
    assert(fs.existsSync(CHROME), `Chrome not found: ${CHROME}`);
    const server = await fetch(BASE_URL);
    assert.equal(server.status, 200, 'Flask server must be running before checks');
    const chrome = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-sandbox', `--remote-debugging-port=${DEBUG_PORT}`, `--user-data-dir=${PROFILE_DIR}`, '--no-first-run', '--no-default-browser-check', '--disable-background-networking', '--disable-sync', '--disable-component-update', '--disable-default-apps', '--metrics-recording-only', '--mute-audio', 'about:blank'], { windowsHide: true, stdio: 'ignore' });
    try {
        await poll(async () => (await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/version`)).ok, 'Chrome DevTools endpoint');
        await verifyLayout(390, 844, 'mobile.png');
        await verifyLayout(320, 640);
        await verifyLayout(768, 1024);
        await verifyLayout(1440, 1000, 'desktop.png');
        await verifyLayout(844, 390);
        await verifyInteraction();
        await verifyVoice();
        await verifyLongConversation();
        await verifySuggestions();
        await verifyAnimations();
        fs.writeFileSync(path.join(CHECKS_DIR, 'ui-smoke-results.json'), JSON.stringify({ ok: true, results }, null, 2));
        console.log(`Completed ${results.length} checks. Screenshots: .checks/mobile.png, .checks/desktop.png, .checks/conversation-mobile.png and .checks/listening-mobile.png`);
    } finally {
        try {
            const version = await (await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/version`)).json();
            const browser = await CDP.connect(version.webSocketDebuggerUrl);
            await browser.send('Browser.close').catch(() => {});
            browser.close();
        } catch {}
        if (chrome.exitCode === null) chrome.kill();
    }
}

main().catch(error => {
    fs.writeFileSync(path.join(CHECKS_DIR, 'ui-smoke-results.json'), JSON.stringify({ ok: false, results, error: error.message }, null, 2));
    console.error(error.stack);
    process.exitCode = 1;
});
