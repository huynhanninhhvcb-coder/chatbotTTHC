const chatMessages = document.getElementById("chatMessages");
const micBtn = document.getElementById("micBtn");
const textInput = document.getElementById("textInput");
const sendTextBtn = document.getElementById("sendTextBtn");
const statusDot = document.getElementById("statusDot");
const statusText = document.getElementById("statusText");
const micHint = document.getElementById("micHint");
const voiceTranscript = document.getElementById("voiceTranscript");
const audioToggleBtn = document.getElementById("audioToggleBtn");
const menuBtn = document.getElementById("menuBtn");
const chatMenu = document.getElementById("chatMenu");
const appShell = document.querySelector(".app-shell");
const SpeechRecognitionAPI =
  window.SpeechRecognition || window.webkitSpeechRecognition;

let recognition = null;
let isListening = false;
let recognitionPending = false;
let recognitionStopping = false;
let ignoreRecognitionResults = false;
let voiceQuestionSent = false;
let currentUtterance = null;
let readAnswers = true;
let activity = "ready";
let isRequestPending = false;
let isClearing = false;
let activeRequest = null;
let cancelReveal = null;
let conversationVersion = 0;
let contextResetRequired = false;
let contextResetPromise = null;

function escapeHtml(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (character) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[character],
  );
}

function formatLinks(text) {
  const linkPattern = /https?:\/\/[^\s<>"'`]+/g;
  let html = "";
  let position = 0;
  for (const match of text.matchAll(linkPattern)) {
    html += escapeHtml(text.slice(position, match.index));
    const url = match[0].replace(/[.,;:!?\)\]\}]+$/, "");
    const trailing = match[0].slice(url.length);
    html += `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(url)}</a>${escapeHtml(trailing)}`;
    position = match.index + match[0].length;
  }
  return html + escapeHtml(text.slice(position));
}

function formatInline(text) {
  const boldPattern = /\*\*(.+?)\*\*/g;
  let html = "";
  let position = 0;
  for (const match of text.matchAll(boldPattern)) {
    html += formatLinks(text.slice(position, match.index));
    html += `<strong>${formatLinks(match[1])}</strong>`;
    position = match.index + match[0].length;
  }
  return html + formatLinks(text.slice(position));
}

function formatMessage(text) {
  const lines = String(text).replace(/\r\n?/g, "\n").split("\n");
  let html = "";
  let paragraph = [];
  let listType = null;
  let sectionOpen = false;
  const flushParagraph = () => {
    if (paragraph.length)
      html += `<p>${paragraph.map(formatInline).join("<br>")}</p>`;
    paragraph = [];
  };
  const closeList = () => {
    if (listType) html += `</${listType}>`;
    listType = null;
  };
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      flushParagraph();
      closeList();
      continue;
    }
    const heading =
      line.match(/^#{1,6}\s+(.+)$/) || line.match(/^\*\*(.{1,100}?)\*\*:?$/);
    if (heading && !/[.!?]$/.test(heading[1])) {
      flushParagraph();
      closeList();
      if (sectionOpen) html += "</div></section>";
      html += `<section class="answer-section"><h3 class="answer-section-title">${formatInline(heading[1])}</h3><div class="answer-section-body">`;
      sectionOpen = true;
      continue;
    }
    const numbered = line.match(/^(\d+)[.)]\s+(.+)$/);
    const bullet = line.match(/^[-*•]\s+(.+)$/);
    if (numbered || bullet) {
      flushParagraph();
      const nextList = numbered ? "ol" : "ul";
      if (listType !== nextList) {
        closeList();
        html += numbered
          ? '<ol class="numbered-list">'
          : '<ul class="answer-list">';
        listType = nextList;
      }
      html += numbered
        ? `<li class="numbered-line" value="${numbered[1]}"><span class="number-badge" aria-hidden="true">${numbered[1]}</span><span>${formatInline(numbered[2])}</span></li>`
        : `<li>${formatInline(bullet[1])}</li>`;
      continue;
    }
    closeList();
    paragraph.push(line);
  }
  flushParagraph();
  closeList();
  if (sectionOpen) html += "</div></section>";
  return html;
}

function nowStamp() {
  return new Date().toLocaleTimeString("vi-VN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function scrollToLatest() {
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function playControlAnimation(element, className) {
  if (
    !element ||
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
  )
    return;
  element.classList.remove(className);
  void element.offsetWidth;
  element.classList.add(className);
  element.addEventListener(
    "animationend",
    () => element.classList.remove(className),
    {
      once: true,
    },
  );
}

function createBotAvatar() {
  const avatar = document.createElement("div");
  avatar.className = "avatar bot-avatar";
  avatar.setAttribute("aria-hidden", "true");
  const crop = document.createElement("span");
  crop.className = "avatar-logo";
  const logo = document.createElement("img");
  logo.src = appShell.dataset.logoUrl;
  logo.alt = "";
  logo.loading = "lazy";
  crop.appendChild(logo);
  avatar.appendChild(crop);
  return avatar;
}

function hideTranscript() {
  if (!voiceTranscript) return;
  voiceTranscript.hidden = true;
  voiceTranscript.textContent = "";
  voiceTranscript.classList.remove("final");
}

function setMicrophoneVisual(listening) {
  micBtn.classList.toggle("listening", listening);
  micBtn.setAttribute("aria-pressed", String(listening));
  micBtn.setAttribute(
    "aria-label",
    listening ? "Dừng nghe giọng nói" : "Đặt câu hỏi bằng giọng nói",
  );
  micBtn.title = listening ? "Dừng nghe" : "Hỏi bằng giọng nói";
  appShell.classList.toggle("listening", listening);
}

function setState(nextState, text, hint) {
  if (nextState === "ready" && !recognition) nextState = "unsupported";
  activity = nextState;
  appShell.dataset.state = nextState;
  const states = {
    ready: [
      "Nhấn micro để nói",
      "Nhấn micro trong ô nhập rồi nói câu hỏi của bạn.",
    ],
    starting: [
      "Đang mở micro…",
      "Cho phép truy cập micro nếu trình duyệt yêu cầu.",
    ],
    listening: [
      "Đang lắng nghe…",
      "Nói rõ câu hỏi. Nhấn micro lần nữa để kết thúc.",
    ],
    processing: [
      "Đang tra cứu…",
      "Tôi đang tìm thông tin cho bạn. Vui lòng chờ một chút.",
    ],
    speaking: [
      "Đang đọc câu trả lời…",
      "Bạn có thể nhấn micro để đặt câu hỏi tiếp theo.",
    ],
    error: [
      "Vui lòng thử lại hoặc nhập tin nhắn",
      "Nhấn micro để thử lại, hoặc nhập câu hỏi trong ô bên trên.",
    ],
    unsupported: [
      "Bạn có thể nhập tin nhắn để tra cứu",
      "Trình duyệt này chưa hỗ trợ nhận diện giọng nói. Hãy dùng trình duyệt hỗ trợ micro hoặc nhập câu hỏi.",
    ],
  };
  const [defaultText, defaultHint] = states[nextState] || states.ready;
  if (statusText) statusText.textContent = text || defaultText;
  if (statusDot) statusDot.className = `status-dot ${nextState}`;
  if (micHint) micHint.textContent = hint || defaultHint;
  const listening = nextState === "listening" || nextState === "starting";
  setMicrophoneVisual(listening);
  if (!listening) hideTranscript();
}

function updateControls() {
  const busy = isRequestPending || isClearing;
  sendTextBtn.disabled = busy;
  sendTextBtn.setAttribute("aria-busy", String(busy));
  micBtn.disabled = busy || recognitionStopping;
  chatMessages.setAttribute("aria-busy", String(busy));
  const clearBtn = document.getElementById("clearBtn");
  if (clearBtn) {
    clearBtn.disabled = isClearing;
    clearBtn.setAttribute("aria-busy", String(isClearing));
  }
  chatMessages.querySelectorAll(".chip-btn").forEach((button) => {
    button.disabled = busy;
  });
}

function renderMessage({
  content,
  isUser = false,
  suggestions,
  scroll = true,
}) {
  const message = document.createElement("div");
  message.className = `message ${isUser ? "user" : "bot"}`;
  const wrapper = document.createElement("div");
  wrapper.className = "message-wrapper";
  const contentElement = document.createElement("div");
  contentElement.className = "message-content";
  if (isUser) {
    const text = document.createElement("p");
    text.textContent = content;
    contentElement.appendChild(text);
  } else {
    contentElement.innerHTML = formatMessage(content);
    wrapper.appendChild(createBotAvatar());
  }
  wrapper.appendChild(contentElement);
  message.appendChild(wrapper);

  if (!isUser && Array.isArray(suggestions) && suggestions.length) {
    const quickReplies = document.createElement("div");
    quickReplies.className = "quick-replies";
    suggestions
      .filter((suggestion) => typeof suggestion === "string")
      .forEach((suggestion) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "chip-btn";
        button.textContent = suggestion;
        button.disabled = isRequestPending || isClearing;
        button.addEventListener("click", () => sendMessage(suggestion));
        quickReplies.appendChild(button);
      });
    message.appendChild(quickReplies);
  }

  const time = document.createElement("div");
  time.className = "timestamp";
  time.textContent = nowStamp();
  if (isUser) contentElement.appendChild(time);
  else message.appendChild(time);
  chatMessages.appendChild(message);
  if (scroll) scrollToLatest();
  return contentElement;
}

function resetToWelcome() {
  chatMessages.replaceChildren();
  renderMessage({
    content:
      "Xin chào! Tôi là Trợ lý hành chính công của Phường Minh Phụng.\n\n" +
      "Tôi có thể hỗ trợ tra cứu thủ tục hành chính, hướng dẫn hồ sơ, thời hạn và các thông tin phục vụ người dân. Anh/Chị cần hỗ trợ vấn đề gì?",
    scroll: false,
  });
  chatMessages.scrollTop = 0;
}

function showTypingIndicator() {
  if (document.getElementById("typingIndicator")) return;
  const typing = document.createElement("div");
  typing.className = "message bot";
  typing.id = "typingIndicator";
  typing.setAttribute("aria-label", "Trợ lý đang tìm câu trả lời");
  const wrapper = document.createElement("div");
  wrapper.className = "message-wrapper";
  const dots = document.createElement("div");
  dots.className = "typing-indicator";
  dots.setAttribute("aria-hidden", "true");
  dots.innerHTML =
    '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>';
  wrapper.append(createBotAvatar(), dots);
  typing.appendChild(wrapper);
  chatMessages.appendChild(typing);
  scrollToLatest();
}

function hideTypingIndicator() {
  document.getElementById("typingIndicator")?.remove();
}

function revealText(element, fullText, version) {
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
    element.innerHTML = formatMessage(fullText);
    scrollToLatest();
    return Promise.resolve(true);
  }
  const words = fullText.split(" ");
  let index = 0;
  let timer = null;
  return new Promise((resolve) => {
    const finish = (completed) => {
      clearTimeout(timer);
      if (cancelReveal === cancel) cancelReveal = null;
      resolve(completed);
    };
    const cancel = () => finish(false);
    cancelReveal = cancel;
    function tick() {
      if (version !== conversationVersion) return finish(false);
      index = Math.min(words.length, index + 5);
      element.innerHTML = formatMessage(words.slice(0, index).join(" "));
      scrollToLatest();
      if (index < words.length) timer = setTimeout(tick, 24);
      else finish(true);
    }
    tick();
  });
}

function stopSpeaking() {
  const wasSpeaking = activity === "speaking";
  currentUtterance = null;
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  if (wasSpeaking) setState("ready");
}

function speak(text) {
  if (
    !readAnswers ||
    !("speechSynthesis" in window) ||
    typeof SpeechSynthesisUtterance === "undefined"
  ) {
    setState("ready");
    return;
  }
  stopSpeaking();
  const cleanText = text
    .replace(/https?:\/\/\S+/g, "đường dẫn trong câu trả lời")
    .replace(/\p{Extended_Pictographic}/gu, "")
    .replace(/[\uFE0F•*#]/g, "")
    .trim();
  if (!cleanText) return setState("ready");
  const utterance = new SpeechSynthesisUtterance(cleanText);
  utterance.lang = "vi-VN";
  utterance.rate = 0.9;
  utterance.pitch = 1;
  const vietnameseVoice = window.speechSynthesis
    .getVoices()
    .find((voice) => voice.lang.toLowerCase().startsWith("vi"));
  if (vietnameseVoice) utterance.voice = vietnameseVoice;
  utterance.onstart = () => {
    if (currentUtterance === utterance) setState("speaking");
  };
  utterance.onend = () => {
    if (currentUtterance !== utterance) return;
    currentUtterance = null;
    if (activity === "speaking") setState("ready");
  };
  utterance.onerror = (event) => {
    if (currentUtterance !== utterance) return;
    currentUtterance = null;
    if (event.error === "canceled" || event.error === "interrupted") {
      if (activity === "speaking") setState("ready");
      return;
    }
    setState(
      "error",
      "Chưa thể đọc thành tiếng. Bạn có thể xem câu trả lời bên trên.",
    );
  };
  currentUtterance = utterance;
  setState("speaking");
  try {
    window.speechSynthesis.speak(utterance);
  } catch (error) {
    currentUtterance = null;
    setState(
      "error",
      "Chưa thể đọc thành tiếng. Bạn có thể xem câu trả lời bên trên.",
    );
  }
}

function stopRecognition(discard = true) {
  if (
    !recognition ||
    !(isListening || recognitionPending || recognitionStopping)
  )
    return;
  ignoreRecognitionResults = discard;
  recognitionStopping = true;
  updateControls();
  try {
    if (discard) recognition.abort();
    else recognition.stop();
  } catch (error) {
    isListening = false;
    recognitionPending = false;
    recognitionStopping = false;
    setMicrophoneVisual(false);
    hideTranscript();
    if (activity === "listening" || activity === "starting") setState("ready");
    updateControls();
  }
}

async function ensureContextReset() {
  if (!contextResetRequired) return;
  if (!contextResetPromise) {
    contextResetPromise = (async () => {
      const response = await fetch("/clear-context", { method: "POST" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      contextResetRequired = false;
    })().finally(() => {
      contextResetPromise = null;
    });
  }
  await contextResetPromise;
}

async function sendMessage(message, { fromVoice = false } = {}) {
  const question = String(message || "").trim();
  if (!question || isRequestPending || isClearing) return false;
  if (!fromVoice) stopRecognition();
  stopSpeaking();
  isRequestPending = true;
  const version = conversationVersion;
  const controller = new AbortController();
  activeRequest = controller;
  updateControls();
  setState("processing");
  renderMessage({ content: question, isUser: true });
  showTypingIndicator();
  try {
    await ensureContextReset();
    if (version !== conversationVersion) return false;
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: question }),
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (version !== conversationVersion) return false;
    if (typeof data.reply !== "string")
      throw new Error("Câu trả lời không hợp lệ");
    hideTypingIndicator();
    const contentElement = renderMessage({
      content: "",
      suggestions: data.suggestions,
    });
    const revealed = await revealText(contentElement, data.reply, version);
    if (!revealed || version !== conversationVersion) return false;
    isRequestPending = false;
    updateControls();
    if (!data.reply.includes("Xin chào") && !data.reply.includes("Tạm biệt"))
      speak(data.reply);
    else setState("ready");
    return true;
  } catch (error) {
    if (version !== conversationVersion || error.name === "AbortError")
      return false;
    hideTypingIndicator();
    renderMessage({
      content:
        "Chưa thể kết nối với trợ lý. Bạn vui lòng kiểm tra mạng và gửi lại câu hỏi.",
    });
    setState(
      "error",
      "Chưa thể kết nối. Hãy kiểm tra mạng và gửi lại câu hỏi.",
    );
    return false;
  } finally {
    if (version === conversationVersion) {
      isRequestPending = false;
      if (activeRequest === controller) activeRequest = null;
      updateControls();
    }
  }
}

async function clearConversation() {
  if (isClearing) return;
  isClearing = true;
  conversationVersion += 1;
  activeRequest?.abort();
  activeRequest = null;
  cancelReveal?.();
  stopRecognition();
  stopSpeaking();
  isRequestPending = false;
  contextResetRequired = true;
  textInput.value = "";
  hideTranscript();
  resetToWelcome();
  updateControls();
  setState("processing", "Đang bắt đầu cuộc trò chuyện mới…");
  try {
    await ensureContextReset();
    setState("ready");
  } catch (error) {
    setState("error", "Chưa thể làm mới. Hãy kiểm tra kết nối mạng.");
  } finally {
    isClearing = false;
    updateControls();
    textInput.focus();
  }
}

function showTranscript(text, final = false) {
  if (!voiceTranscript) return;
  voiceTranscript.hidden = false;
  voiceTranscript.textContent = text;
  voiceTranscript.classList.toggle("final", final);
}

function startMicrophone() {
  if (!recognition) {
    setState(
      "unsupported",
      "Hãy dùng Chrome hoặc Edge để hỏi bằng giọng nói, hoặc nhập tin nhắn.",
    );
    textInput.focus();
    return;
  }
  if (isRequestPending || isClearing || recognitionStopping) return;
  if (isListening || recognitionPending) {
    stopRecognition(false);
    return;
  }
  stopSpeaking();
  ignoreRecognitionResults = false;
  voiceQuestionSent = false;
  recognitionPending = true;
  setState("starting");
  showTranscript("Bạn hãy nói câu hỏi…");
  try {
    recognition.start();
  } catch (error) {
    recognitionPending = false;
    setState("error", "Chưa thể mở micro. Kiểm tra quyền micro rồi thử lại.");
  }
}

if (SpeechRecognitionAPI) {
  try {
    recognition = new SpeechRecognitionAPI();
    recognition.lang = "vi-VN";
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    recognition.onstart = () => {
      recognitionPending = false;
      isListening = true;
      if (ignoreRecognitionResults) return stopRecognition();
      setState("listening");
    };
    recognition.onresult = (event) => {
      if (ignoreRecognitionResults || voiceQuestionSent) return;
      let finalText = "";
      let interimText = "";
      for (let index = 0; index < event.results.length; index += 1) {
        const result = event.results[index];
        if (result.isFinal) finalText += `${result[0].transcript} `;
        else interimText += `${result[0].transcript} `;
      }
      const transcript = `${finalText}${interimText}`.trim();
      if (transcript) showTranscript(transcript, Boolean(finalText.trim()));
      if (finalText.trim()) {
        voiceQuestionSent = true;
        stopRecognition(false);
        sendMessage(finalText.trim(), { fromVoice: true });
      }
    };
    recognition.onerror = (event) => {
      if (ignoreRecognitionResults || voiceQuestionSent) return;
      const errors = {
        "not-allowed":
          "Chưa có quyền micro. Cho phép micro trong trình duyệt rồi thử lại.",
        "service-not-allowed":
          "Chưa được phép nhận diện giọng nói. Bạn có thể nhập tin nhắn.",
        "no-speech": "Chưa nghe thấy giọng nói. Nhấn micro để nói lại.",
        network: "Kết nối giọng nói gián đoạn. Kiểm tra mạng rồi thử lại.",
        "audio-capture":
          "Không tìm thấy micro. Kiểm tra micro hoặc nhập tin nhắn.",
        "language-not-supported":
          "Chưa hỗ trợ giọng nói tiếng Việt. Bạn có thể nhập tin nhắn.",
        aborted: "Đã dừng nghe. Nhấn micro khi bạn muốn hỏi tiếp.",
      };
      setState(
        "error",
        errors[event.error] ||
          "Chưa nhận diện được giọng nói. Nhấn micro để thử lại.",
      );
    };
    recognition.onend = () => {
      isListening = false;
      recognitionPending = false;
      recognitionStopping = false;
      setMicrophoneVisual(false);
      hideTranscript();
      updateControls();
      if (
        !voiceQuestionSent &&
        !ignoreRecognitionResults &&
        (activity === "listening" || activity === "starting")
      ) {
        setState("error", "Chưa nhận được câu hỏi. Nhấn micro rồi nói lại.");
      }
    };
  } catch (error) {
    recognition = null;
  }
}

function submitTextInput() {
  const question = textInput.value.trim();
  if (!question || isRequestPending || isClearing) return;
  playControlAnimation(sendTextBtn, "is-sending");
  textInput.value = "";
  sendMessage(question);
}

function updateAudioToggle() {
  if (!audioToggleBtn) return;
  audioToggleBtn.setAttribute("aria-pressed", String(readAnswers));
  audioToggleBtn.setAttribute(
    "aria-label",
    readAnswers ? "Tắt đọc câu trả lời" : "Bật đọc câu trả lời",
  );
  audioToggleBtn.title = readAnswers
    ? "Tắt đọc câu trả lời"
    : "Bật đọc câu trả lời";
  const label = audioToggleBtn.querySelector("[data-audio-label]");
  if (label)
    label.textContent = `Đọc câu trả lời: ${readAnswers ? "Bật" : "Tắt"}`;
}

function closeMenu(restoreFocus = false) {
  if (!chatMenu || !menuBtn) return;
  chatMenu.hidden = true;
  menuBtn.setAttribute("aria-expanded", "false");
  if (restoreFocus) menuBtn.focus();
}

menuBtn?.addEventListener("click", () => {
  if (!chatMenu) return;
  const opening = chatMenu.hidden;
  chatMenu.hidden = !opening;
  menuBtn.setAttribute("aria-expanded", String(opening));
  if (opening) chatMenu.querySelector("button:not(:disabled)")?.focus();
});
document.addEventListener("click", (event) => {
  if (
    chatMenu &&
    !chatMenu.hidden &&
    !chatMenu.contains(event.target) &&
    !menuBtn.contains(event.target)
  )
    closeMenu();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && chatMenu && !chatMenu.hidden) {
    event.preventDefault();
    closeMenu(true);
  }
});
chatMenu?.addEventListener("focusout", (event) => {
  if (
    event.relatedTarget &&
    !chatMenu.contains(event.relatedTarget) &&
    !menuBtn.contains(event.relatedTarget)
  )
    closeMenu();
});

micBtn.addEventListener("click", startMicrophone);
sendTextBtn.addEventListener("click", submitTextInput);
textInput.addEventListener("input", updateControls);
textInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.isComposing) {
    event.preventDefault();
    submitTextInput();
  }
});
document.getElementById("clearBtn")?.addEventListener("click", () => {
  closeMenu();
  clearConversation();
});
document.getElementById("voiceGuideBtn")?.addEventListener("click", () => {
  closeMenu();
  const guide = recognition
    ? "Nhấn micro trong ô nhập, cho phép truy cập rồi nói câu hỏi."
    : "Dùng trình duyệt hỗ trợ micro hoặc nhập tin nhắn để tra cứu.";
  if (statusText) statusText.textContent = guide;
  if (micHint) micHint.textContent = guide;
  micBtn.focus();
});
document.querySelectorAll("[data-question]").forEach((button) => {
  button.addEventListener("click", () => sendMessage(button.dataset.question));
});
audioToggleBtn?.addEventListener("click", () => {
  readAnswers = !readAnswers;
  if (!readAnswers) stopSpeaking();
  updateAudioToggle();
  closeMenu(true);
});
document.getElementById("bhytBtn")?.addEventListener("click", () => {
  window.open(
    "https://baohiemxahoi.gov.vn/tracuu/Pages/tra-cuu-thoi-han-su-dung-the-bhyt.aspx",
    "_blank",
    "noopener",
  );
});
document.getElementById("mauDonBtn")?.addEventListener("click", () => {
  window.open("/mau-don", "_blank", "noopener");
});
document.getElementById("khuPhoBtn")?.addEventListener("click", () => {
  window.open(
    "https://sites.google.com/view/phuongminhphung/trang-ch%E1%BB%A7?authuser=0",
    "_blank",
    "noopener",
  );
});

resetToWelcome();
updateAudioToggle();
updateControls();
setState(recognition ? "ready" : "unsupported");
