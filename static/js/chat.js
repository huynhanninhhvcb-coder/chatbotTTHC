const chatMessages = document.getElementById("chatMessages");
const micBtn = document.getElementById("micBtn");
const textInput = document.getElementById("textInput");
const sendTextBtn = document.getElementById("sendTextBtn");
const statusDot = document.getElementById("statusDot");
const statusText = document.getElementById("statusText");
const composerStatus = document.getElementById("composerStatus");
const micHint = document.getElementById("micHint");
const voiceTranscript = document.getElementById("voiceTranscript");
const audioToggleBtn = document.getElementById("audioToggleBtn");
const menuBtn = document.getElementById("menuBtn");
const chatMenu = document.getElementById("chatMenu");
const appShell = document.querySelector(".app-shell");
const formFileInput = document.getElementById("formFileInput");
const attachBtn = document.getElementById("attachBtn");
const dienDonBtn = document.getElementById("dienDonBtn");
const SpeechRecognitionAPI =
  window.SpeechRecognition || window.webkitSpeechRecognition;
const DEFAULT_PLACEHOLDER = textInput.placeholder;
// Nút câu hỏi mẫu trên màn hình chào. Nút hiện nhãn ngắn, còn "question" là câu
// gửi đi: 3 câu đầu trùng câu đã soạn sẵn trong tao_cau_tra_loi_san.py nên có
// câu trả lời ngay (sửa câu ở đó thì sửa cả ở đây); câu cuối do AI tra trực tiếp.
const WELCOME_QUESTIONS = [
  {
    label: "Đăng ký khai sinh cần gì?",
    question: "Thủ tục đăng ký khai sinh cần những gì?",
    icon: "baby",
  },
  {
    label: "Thủ tục kết hôn ra sao?",
    question: "Thủ tục đăng ký kết hôn cần những gì?",
    icon: "rings",
  },
  {
    label: "Tạm trú cần giấy tờ gì?",
    question: "Đăng ký tạm trú cần giấy tờ gì?",
    icon: "home",
  },
  {
    label: "Cách nộp hồ sơ trực tuyến?",
    question: "Cách nộp hồ sơ thủ tục hành chính trực tuyến như thế nào?",
    icon: "laptop",
  },
];
const WELCOME_ICONS = {
  baby: '<circle cx="12" cy="7" r="3.6"/><path d="M12 3.4c.6-1.5 2.4-1.9 3.2-.6"/><path d="M6.6 21v-2.6a5.4 5.4 0 0 1 10.8 0V21"/><path d="m3.8 14.6 3.4-2.3m13 2.3-3.4-2.3"/>',
  rings:
    '<circle cx="8.6" cy="14.4" r="5.4"/><circle cx="15.4" cy="14.4" r="5.4"/><path d="m10.2 5.6 1.8-2.4 1.8 2.4-1.8 2Z"/>',
  home: '<path d="M3 11.6 12 4l9 7.6"/><path d="M5.6 10v10h12.8V10"/><path d="M10 20v-5.4h4V20"/>',
  laptop:
    '<rect x="4.2" y="5" width="15.6" height="11" rx="1.6"/><path d="M2 19.6h20"/>',
};
const CHEVRON_ICON =
  '<svg class="welcome-chip-chevron" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m9 5 7 7-7 7"/></svg>';

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
let conversationVersion = 0;
// Lịch sử hỏi-đáp gần nhất, gửi kèm mỗi câu hỏi để AI hiểu câu hỏi nối tiếp.
const MAX_HISTORY_MESSAGES = 8; // = MAX_HISTORY_MESSAGES trong app.py
let chatHistory = [];
// Server gửi kèm danh sách câu hỏi gợi ý (JSON) sau ký tự này, ở cuối câu
// trả lời (xem SUGGESTIONS_SEPARATOR trong ai_engine.py).
const SUGGESTIONS_SEPARATOR = "\x1e";
const FORM_API = "/dien-don/api";
// Danh mục mẫu đơn lấy từ máy chủ (/dien-don/api/templates): nhóm, tên ngắn,
// từ khoá nhận diện, trường bắt buộc và nhãn hiển thị. Nhờ vậy chatbot luôn
// khớp với các mẫu trong thư mục templates_word mà không phải sửa tay ở đây.
let formCatalog = null;
let formCatalogRequest = null;
let formFlow = null;
let pendingDocumentContext = "";
let documentAiConsent = false;

function loadFormCatalog() {
  if (formCatalog) return Promise.resolve(formCatalog);
  if (!formCatalogRequest) {
    formCatalogRequest = fetch(`${FORM_API}/templates`)
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((catalog) => {
        formCatalog = catalog;
        return catalog;
      })
      .catch((error) => {
        formCatalogRequest = null;
        throw error;
      });
  }
  return formCatalogRequest;
}

function catalogTemplate(id) {
  return formCatalog?.templates.find((item) => item.id === id) || null;
}

function familyTemplates(familyId) {
  return (formCatalog?.templates || []).filter(
    (item) => item.family === familyId,
  );
}

// Mỗi "family" chỉ hiện một nút (bản tự khai và khai thay hỏi sau).
function groupFamilies(groupId) {
  const seen = new Set();
  return (formCatalog?.templates || []).filter((item) => {
    if (item.group !== groupId || seen.has(item.family)) return false;
    seen.add(item.family);
    return true;
  });
}

function fieldLabel(key, templateId = formFlow?.templateId) {
  const field = catalogTemplate(templateId)?.fields.find(
    (item) => item.name === key,
  );
  return field?.label || formCatalog?.field_labels?.[key] || key;
}

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
  logo.src = appShell.dataset.avatarUrl || appShell.dataset.logoUrl;
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
      "Nhấn nút micro màu đỏ rồi nói câu hỏi của bạn.",
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
  // Lúc chờ, dòng trạng thái ẩn đi (ô nhập đã có lời nhắc); chỉ hiện khi đang
  // nghe/tra cứu/đọc, khi lỗi, hoặc khi có hướng dẫn riêng (is-notice).
  composerStatus?.classList.remove("is-notice");
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
  if (attachBtn) attachBtn.disabled = busy;
  if (dienDonBtn) dienDonBtn.disabled = busy;
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

function renderMessage({ content, isUser = false, scroll = true }) {
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

  const time = document.createElement("div");
  time.className = "timestamp";
  time.textContent = nowStamp();
  if (isUser) contentElement.appendChild(time);
  else message.appendChild(time);
  chatMessages.appendChild(message);
  if (scroll) scrollToLatest();
  return contentElement;
}

// Thêm nút câu hỏi gợi ý dưới câu trả lời (gọi sau khi stream xong, vì danh
// sách gợi ý đến cuối cùng). Bấm nút = gửi đúng câu đó như người dùng tự hỏi.
function appendSuggestions(contentElement, suggestions) {
  const items = suggestions.filter(
    (suggestion) => typeof suggestion === "string" && suggestion.trim(),
  );
  if (!items.length) return;
  const message = contentElement.closest(".message");
  const quickReplies = document.createElement("div");
  quickReplies.className = "quick-replies";
  items.forEach((suggestion) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chip-btn";
    button.textContent = suggestion;
    button.disabled = isRequestPending || isClearing;
    button.addEventListener("click", () => sendMessage(suggestion));
    quickReplies.appendChild(button);
  });
  message.insertBefore(
    quickReplies,
    message.querySelector(":scope > .timestamp"),
  );
}

function appendFormActions(contentElement, actions) {
  const message = contentElement.closest(".message");
  const row = document.createElement("div");
  row.className = "quick-replies form-actions";
  actions.forEach(({ label, action, primary = false }) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `chip-btn${primary ? " primary-chip" : ""}`;
    button.textContent = label;
    button.addEventListener("click", action);
    row.appendChild(button);
  });
  message.insertBefore(row, message.querySelector(":scope > .timestamp"));
  scrollToLatest();
}

async function startFormAssistant() {
  stopSpeaking();
  discardFormInterview();
  const version = conversationVersion;
  formFlow = { stage: "group", templateId: null, sessionId: null, fields: {} };
  try {
    await loadFormCatalog();
    if (version !== conversationVersion) return;
  } catch (error) {
    if (version !== conversationVersion) return;
    formFlow = null;
    renderMessage({
      content: `Chưa tải được danh sách mẫu đơn (${error.message}). Anh/Chị vui lòng thử lại sau ít phút.`,
    });
    return;
  }
  const content = renderMessage({
    content:
      "Tôi sẽ giúp Anh/Chị điền đơn ngay tại đây. Anh/Chị cần làm loại giấy tờ nào?\n\n" +
      "- **Tờ khai đề nghị hưởng chế độ**: trợ cấp hưu trí xã hội, mai táng, hỏa táng, Nghị quyết 40/32, khuyết tật, BHYT.\n" +
      "- **Đơn xác nhận chưa hưởng chế độ**: khi chuyển nơi ở, cần phường Minh Phụng xác nhận chưa hưởng chế độ tại đây.",
  });
  appendFormActions(
    content,
    formCatalog.groups.map((group) => ({
      label: group.title,
      action: () => chooseFormGroup(group.id),
    })),
  );
}

function chooseFormGroup(groupId, showUserSelection = true) {
  const group = formCatalog?.groups.find((item) => item.id === groupId);
  if (!group) return;
  formFlow = {
    stage: "family",
    groupId,
    templateId: null,
    sessionId: null,
    fields: {},
  };
  if (showUserSelection) renderMessage({ content: group.title, isUser: true });
  const content = renderMessage({ content: "Anh/Chị chọn mẫu cần làm:" });
  appendFormActions(content, [
    ...groupFamilies(groupId).map((template) => ({
      label: template.short_title,
      action: () => chooseFormFamily(template.family),
    })),
    { label: "Quay lại", action: startFormAssistant },
  ]);
}

function chooseFormFamily(
  familyId,
  showUserSelection = true,
  variantHint = null,
) {
  const variants = familyTemplates(familyId);
  if (!variants.length) return;
  if (variants.length === 1) {
    chooseFormTemplate(variants[0].id, showUserSelection);
    return;
  }
  if (showUserSelection)
    renderMessage({ content: variants[0].short_title, isUser: true });
  const hinted = variants.find((item) => item.variant === variantHint);
  if (hinted) {
    chooseFormTemplate(hinted.id, false);
    return;
  }
  formFlow = {
    stage: "variant",
    familyId,
    templateId: null,
    sessionId: null,
    fields: {},
  };
  const content = renderMessage({
    content: "Anh/Chị tự đứng đơn cho bản thân, hay khai thay cho người thân?",
  });
  appendFormActions(content, [
    {
      label: "Tự khai cho bản thân",
      action: () => chooseFormVariant("tu_khai"),
    },
    {
      label: "Khai thay cho người thân",
      action: () => chooseFormVariant("khai_thay"),
    },
  ]);
}

function chooseFormVariant(variant, showUserSelection = true) {
  const template = familyTemplates(formFlow?.familyId).find(
    (item) => item.variant === variant,
  );
  if (!template) return;
  if (showUserSelection)
    renderMessage({
      content:
        variant === "khai_thay"
          ? "Khai thay cho người thân"
          : "Tự khai cho bản thân",
      isUser: true,
    });
  chooseFormTemplate(template.id, false);
}

function chooseFormTemplate(id, showUserSelection = true) {
  const template = catalogTemplate(id);
  if (!template) return;
  formFlow = { stage: "method", templateId: id, sessionId: null, fields: {} };
  if (showUserSelection)
    renderMessage({ content: template.short_title, isUser: true });
  const content = renderMessage({
    content: `Mẫu: **${template.title}**.\n\nAnh/Chị muốn cung cấp thông tin theo cách nào?`,
  });
  appendFormActions(content, [
    { label: "Phỏng vấn", action: () => startFormInterview(false) },
    { label: "Đính kèm tệp giấy tờ", action: () => formFileInput?.click() },
    { label: "Chọn lại mẫu", action: startFormAssistant },
  ]);
}

function normaliseVietnamese(text) {
  return String(text || "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .toLowerCase();
}

// "Đơn xác nhận chưa hưởng..." khác "Tờ khai đề nghị hưởng..." dù cùng chủ đề
// (vd. hưu trí, khuyết tật, Nghị quyết 40/32).
const CONFIRMATION_PATTERN = /xac nhan|chua huong|chua lam ho so|chua nhan/;
const PROXY_PATTERN =
  /khai thay|thay cho|nguoi than|\bcho (cha|me|bo|ong|ba|con|chau|vo|chong)\b/;
const SELF_PATTERN = /tu khai|ban than|\bcho (toi|minh|em)\b/;

function detectFormVariant(value) {
  if (PROXY_PATTERN.test(value)) return "khai_thay";
  if (SELF_PATTERN.test(value)) return "tu_khai";
  return null;
}

function detectRequestedTemplate(text) {
  if (!formCatalog) return null;
  const value = normaliseVietnamese(text);
  const matches = formCatalog.templates.filter((template) =>
    template.keywords.some((keyword) => value.includes(keyword)),
  );
  if (!matches.length) return null;
  const group = CONFIRMATION_PATTERN.test(value) ? "xac_nhan" : "to_khai";
  const preferred = matches.filter((template) => template.group === group);
  const best = (preferred.length ? preferred : matches)[0];
  return { family: best.family, variant: detectFormVariant(value) };
}

function isFormRequest(text) {
  const value = normaliseVietnamese(text);
  const action = [
    "dien don",
    "lam don",
    "tao don",
    "lap don",
    "viet don",
    "khai don",
    "dien to khai",
    "lam to khai",
  ].some((item) => value.includes(item));
  if (action) return true;
  // Câu hỏi tra cứu ("đăng ký BHYT cần giấy tờ gì?") vẫn để AI trả lời.
  return Boolean(
    !QUESTION_PATTERN.test(value) &&
    detectRequestedTemplate(text) &&
    ["muon lam", "dang ky", "de nghi", "ho tro toi", "xin xac nhan"].some(
      (item) => value.includes(item),
    ),
  );
}

// Câu hỏi tra cứu thông thường (không phải câu trả lời cho bước điền đơn).
const QUESTION_PATTERN = /\?|\b(gi|nao|bao lau|o dau|the nao|nhung gi|thu tuc)\b/;
// So khớp trọn từ (\b) để "thôi" không bị hiểu là "hỏi", "nhanh" không thành "ảnh".
const INTERVIEW_REPLY =
  /\b(phong van|hoi dap|tra loi|hoi tung|hoi lan luot)\b|khong co giay to/;
const DOCUMENT_REPLY = /\b(pdf|tep|file|dinh kem|tai len|tai lieu|giay to)\b/;

// Người dân đang ở bước chọn mẫu/cách điền mà gõ một câu hỏi khác: thoát luồng
// điền đơn để câu hỏi đến AI như bình thường (trả về false cho sendMessage).
function leaveFormFlow() {
  formFlow = null;
  return false;
}

function handleFormConversationInput(text) {
  const value = normaliseVietnamese(text);
  const stage = formFlow?.stage;
  if (stage === "group" || stage === "family") {
    const match = detectRequestedTemplate(text);
    let group = null;
    if (stage === "group" && CONFIRMATION_PATTERN.test(value))
      group = "xac_nhan";
    else if (stage === "group" && /to khai|de nghi|huong che do/.test(value))
      group = "to_khai";
    if (!match && !group && QUESTION_PATTERN.test(value))
      return leaveFormFlow();
    renderMessage({ content: text, isUser: true });
    if (match) chooseFormFamily(match.family, false, match.variant);
    else if (group) chooseFormGroup(group, false);
    else
      renderMessage({
        content:
          "Tôi chưa xác định được loại đơn. Anh/Chị có thể bấm chọn ở trên, hoặc nói rõ, ví dụ: tờ khai trợ cấp hưu trí, tờ khai mai táng, đơn xác nhận chưa hưởng mừng thọ.",
      });
    return true;
  }
  if (stage === "variant") {
    let variant = detectFormVariant(value);
    if (!variant && QUESTION_PATTERN.test(value)) return leaveFormFlow();
    if (!variant && /\b(toi|minh)\b/.test(value)) variant = "tu_khai";
    renderMessage({ content: text, isUser: true });
    if (variant) chooseFormVariant(variant, false);
    else
      renderMessage({
        content: "Anh/Chị tự khai cho bản thân, hay khai thay cho người thân?",
      });
    return true;
  }
  if (stage === "method") {
    const interview = INTERVIEW_REPLY.test(value);
    if (!interview && QUESTION_PATTERN.test(value)) return leaveFormFlow();
    renderMessage({ content: text, isUser: true });
    if (interview) startFormInterview(false);
    else if (DOCUMENT_REPLY.test(value)) formFileInput?.click();
    else
      renderMessage({
        content:
          "Anh/Chị muốn tôi hỏi lần lượt trong chat, hay muốn đính kèm tệp giấy tờ (PDF) để tôi đọc thông tin?",
      });
    return true;
  }
  if (!isFormRequest(text)) return false;
  renderMessage({ content: text, isUser: true });
  loadFormCatalog()
    .then(() => {
      const match = detectRequestedTemplate(text);
      if (match) chooseFormFamily(match.family, false, match.variant);
      else startFormAssistant();
    })
    .catch(() => startFormAssistant());
  return true;
}

async function formJson(path, payload) {
  const response = await fetch(`${FORM_API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}

// Hủy phiên phỏng vấn đang dở trên máy chủ (khi bắt đầu lại, đổi mẫu, xóa hội
// thoại hoặc chuyển sang đọc giấy tờ), không chờ kết quả.
function discardFormInterview() {
  const sessionId = formFlow?.sessionId;
  if (!sessionId) return;
  formFlow.sessionId = null;
  fetch(`${FORM_API}/interview/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
    keepalive: true,
  }).catch(() => {});
}

// Các bước điền đơn chạy bất đồng bộ: nếu người dân bấm "Xóa hội thoại" trong
// lúc chờ máy chủ, conversationVersion đổi và kết quả cũ bị bỏ qua (không ghi
// vào hội thoại mới, không mở khóa nút giữa chừng).
async function startFormInterview(supplementOnly = false) {
  if (!formFlow?.templateId || isRequestPending) return;
  const version = conversationVersion;
  isRequestPending = true;
  updateControls();
  showTypingIndicator();
  try {
    const payload = { template_id: formFlow.templateId };
    if (supplementOnly && formFlow.fields) {
      const hasData = Object.values(formFlow.fields).some((v) =>
        String(v || "").trim(),
      );
      if (hasData) payload.prefilled_fields = formFlow.fields;
    }
    discardFormInterview();
    const result = await formJson("/interview/start", payload);
    if (version !== conversationVersion) return;
    hideTypingIndicator();
    formFlow.stage = "interview";
    formFlow.sessionId = result.session_id;
    renderMessage({
      content: `${result.message || "Tôi sẽ hỏi lần lượt từng thông tin."}\n\n**Câu hỏi:** ${result.question}`,
    });
    textInput.placeholder = "Nhập câu trả lời hoặc nói bằng micro…";
    textInput.focus();
  } catch (error) {
    if (version !== conversationVersion) return;
    hideTypingIndicator();
    renderMessage({ content: `Không thể bắt đầu phỏng vấn: ${error.message}` });
  } finally {
    if (version === conversationVersion) {
      isRequestPending = false;
      updateControls();
    }
  }
}

async function answerFormInterview(answer) {
  if (!formFlow?.sessionId || isRequestPending) return false;
  stopSpeaking();
  const version = conversationVersion;
  isRequestPending = true;
  updateControls();
  renderMessage({ content: answer, isUser: true });
  showTypingIndicator();
  try {
    const result = await formJson("/interview/answer", {
      session_id: formFlow.sessionId,
      answer,
    });
    if (version !== conversationVersion) return false;
    hideTypingIndicator();
    if (result.error) {
      renderMessage({
        content: `${result.error}\n\n${result.question || "Vui lòng trả lời lại."}`,
      });
    } else if (result.complete) {
      formFlow.stage = "review";
      formFlow.sessionId = null;
      formFlow.fields = result.fields || {};
      textInput.placeholder = DEFAULT_PLACEHOLDER;
      showFormReview("Đã hoàn tất phỏng vấn.");
    } else {
      const progress = result.progress
        ? ` (${result.progress.current}/${result.progress.total})`
        : "";
      const content = renderMessage({
        content: `**Câu hỏi tiếp theo${progress}:** ${result.question}`,
      });
      speak(result.question || "");
      return true;
    }
    return true;
  } catch (error) {
    if (version !== conversationVersion) return false;
    hideTypingIndicator();
    // Phiên hết hạn (để quá lâu, máy chủ khởi động lại) hoặc mất mạng: dừng
    // phỏng vấn để các câu sau quay về hỏi đáp bình thường, không bị kẹt.
    formFlow.stage = "review";
    formFlow.sessionId = null;
    textInput.placeholder = DEFAULT_PLACEHOLDER;
    const content = renderMessage({
      content: `Không thể ghi nhận câu trả lời (${error.message}). Phiên phỏng vấn đã dừng; Anh/Chị có thể phỏng vấn lại, hoặc tiếp tục hỏi chatbot như bình thường.`,
    });
    appendFormActions(content, [
      {
        label: "Phỏng vấn lại",
        primary: true,
        action: () => startFormInterview(true),
      },
      { label: "Chọn mẫu khác", action: startFormAssistant },
    ]);
    return true;
  } finally {
    if (version === conversationVersion) {
      isRequestPending = false;
      updateControls();
    }
  }
}

function showFormReview(intro, warnings = []) {
  const template = catalogTemplate(formFlow.templateId);
  const filled = (key) => String(formFlow.fields?.[key] || "").trim();
  // Chỉ liệt kê các trường mẫu này dùng, theo thứ tự in trên đơn; giấy tờ
  // quét có thể chứa thêm thông tin không liên quan tới mẫu đang làm.
  const templateFields = (template?.fields || []).map((item) => item.name);
  const keys = templateFields.filter(filled);
  const shown = keys.length
    ? keys
    : Object.keys(formFlow.fields || {}).filter(filled);
  const summary = shown.length
    ? shown
        .map((key) => `- **${fieldLabel(key)}:** ${formFlow.fields[key]}`)
        .join("\n")
    : "Chưa nhận diện được thông tin. Bạn nên chọn phỏng vấn để nhập thủ công.";

  const missing = (template?.required_fields || []).filter(
    (key) => !filled(key),
  );
  const missingText = missing.length
    ? `\n\n⚠️ **Chưa có thông tin bắt buộc (cần bổ sung trước khi tạo đơn):**\n${missing.map((key) => `- ${fieldLabel(key)}`).join("\n")}`
    : "";

  const warningText = warnings.length
    ? `\n\n**Cần lưu ý:**\n${warnings.map((item) => `- ${item}`).join("\n")}`
    : "";
  const content = renderMessage({
    content: `${intro}\n\n**Thông tin chuẩn bị đưa vào đơn:**\n${summary}${missingText}${warningText}\n\nVui lòng kiểm tra trước khi tạo đơn.`,
  });
  const reviewActions = missing.length
    ? [
        {
          label: "Bổ sung thông tin còn thiếu",
          primary: true,
          action: showMissingFields,
        },
        {
          label: "Tạo đơn dù còn thiếu",
          action: () => generateFormDocument({ allowIncomplete: true }),
        },
      ]
    : [
        {
          label: "Tạo và tải đơn Word",
          primary: true,
          action: () => generateFormDocument(),
        },
        {
          label: "Phỏng vấn lại từ đầu",
          action: () => startFormInterview(false),
        },
      ];
  reviewActions.push({ label: "Chọn mẫu khác", action: startFormAssistant });
  appendFormActions(content, reviewActions);
}

// Các bước mà giấy tờ đính kèm dùng để điền đơn đang làm; ngoài các bước này
// (chưa chọn mẫu, hoặc đã tải đơn xong) tệp đính kèm được hỏi đáp như tài liệu.
const FORM_DOCUMENT_STAGES = ["method", "interview", "review"];

async function processDocumentFiles(input) {
  const files = Array.from(input?.files || []);
  if (!files.length || isRequestPending) return;
  const forForm =
    Boolean(formFlow?.templateId) &&
    FORM_DOCUMENT_STAGES.includes(formFlow.stage);
  // Đang chọn mẫu dở mà gửi tài liệu để hỏi: bỏ luồng chọn mẫu, để câu hỏi
  // tiếp theo về tài liệu không bị hiểu là câu trả lời chọn mẫu.
  if (!forForm && ["group", "family", "variant"].includes(formFlow?.stage))
    formFlow = null;
  const version = conversationVersion;
  renderMessage({
    content: `Đã chọn ${files.length} tệp: ${files.map((file) => file.name).join(", ")}`,
    isUser: true,
  });
  isRequestPending = true;
  updateControls();
  setState("processing", "Đang đọc giấy tờ…");
  showTypingIndicator();
  const payload = new FormData();
  files.forEach((file) => payload.append("files", file));
  try {
    const response = await fetch(`${FORM_API}/extract`, {
      method: "POST",
      body: payload,
    });
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(result.error || `HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    if (version !== conversationVersion) return;
    hideTypingIndicator();
    if (forForm) {
      // Gộp với thông tin đã có (câu trả lời phỏng vấn, giấy tờ gửi trước):
      // giấy tờ mới chỉ ghi đè những trường nó đọc được.
      const found = Object.fromEntries(
        Object.entries(result.fields || {}).filter(([, value]) =>
          String(value || "").trim(),
        ),
      );
      if (formFlow.stage === "interview") discardFormInterview();
      formFlow.stage = "review";
      formFlow.fields = { ...formFlow.fields, ...found };
      textInput.placeholder = DEFAULT_PLACEHOLDER;
      showFormReview(
        `Đã đọc xong ${result.processed_file_count || files.length} tệp.`,
        result.warnings || [],
      );
    } else {
      pendingDocumentContext = String(result.raw_text || "").trim();
      const detected = Object.values(result.fields || {}).filter((value) =>
        String(value || "").trim(),
      ).length;
      const documentMessage = renderMessage({
        content: pendingDocumentContext
          ? `Tôi đã đọc tài liệu cục bộ${detected ? ` và nhận diện được ${detected} trường thông tin` : ""}. Để hỏi đáp về tài liệu, Anh/Chị có đồng ý gửi phần văn bản đã trích xuất tới OpenAI để phân tích không?`
          : "Tôi chưa đọc được nội dung rõ ràng. Anh/Chị có thể gửi tệp rõ nét hơn (tốt nhất là tệp PDF có sẵn chữ), hoặc chọn phỏng vấn để trả lời từng câu.",
      });
      documentAiConsent = false;
      if (pendingDocumentContext) {
        appendFormActions(documentMessage, [
          {
            label: "Đồng ý và hỏi về tài liệu",
            primary: true,
            action: () => {
              documentAiConsent = true;
              renderMessage({
                content: "Tôi đồng ý cho AI phân tích nội dung tài liệu.",
                isUser: true,
              });
              renderMessage({
                content:
                  "Cảm ơn Anh/Chị. Bây giờ Anh/Chị hãy nhập hoặc nói câu hỏi về tài liệu.",
              });
              textInput.placeholder = "Nhập câu hỏi về tài liệu vừa gửi…";
              textInput.focus();
            },
          },
          {
            label: "Không đồng ý",
            action: () => {
              pendingDocumentContext = "";
              documentAiConsent = false;
              renderMessage({
                content: "Tôi sẽ không gửi nội dung tài liệu tới AI.",
              });
            },
          },
        ]);
      }
    }
    setState("ready");
  } catch (error) {
    if (version !== conversationVersion) return;
    hideTypingIndicator();
    if (error.status === 503) {
      // Máy chủ không có Tesseract (Render): chỉ đọc được PDF có sẵn chữ.
      const content = renderMessage({
        content:
          "Máy chủ hiện chưa đọc được ảnh chụp hoặc bản scan. Anh/Chị vui lòng đính kèm tệp PDF có sẵn chữ (ví dụ tệp xuất từ Word), hoặc chọn phỏng vấn để trả lời từng câu.",
      });
      if (formFlow?.templateId)
        appendFormActions(content, [
          {
            label: "Phỏng vấn",
            primary: true,
            action: () => startFormInterview(true),
          },
          { label: "Chọn mẫu khác", action: startFormAssistant },
        ]);
      setState("error", "Chưa đọc được ảnh. Hãy gửi PDF có chữ.");
    } else {
      renderMessage({ content: `Không thể đọc giấy tờ: ${error.message}` });
      setState("error", "Không thể đọc giấy tờ. Vui lòng thử lại.");
    }
  } finally {
    input.value = "";
    if (version === conversationVersion) {
      isRequestPending = false;
      updateControls();
    }
  }
}

// Danh sách thông tin còn thiếu/chưa đúng, kèm 2 lựa chọn bổ sung hoặc đổi mẫu.
function showMissingInfoPrompt(fieldErrors) {
  const lines = Object.entries(fieldErrors)
    .map(([key, message]) => `- **${fieldLabel(key)}:** ${message}`)
    .join("\n");
  const content = renderMessage({
    content: `Cần bổ sung thông tin trước khi tạo đơn:\n\n${lines}`,
  });
  appendFormActions(content, [
    {
      label: "Phỏng vấn để bổ sung",
      primary: true,
      action: () => startFormInterview(true),
    },
    { label: "Chọn mẫu khác", action: startFormAssistant },
  ]);
}

// Nút "Bổ sung thông tin còn thiếu": hỏi máy chủ những trường còn thiếu (cùng
// quy tắc lúc tạo đơn); không hỏi được thì dùng danh sách trường bắt buộc.
async function showMissingFields() {
  if (!formFlow?.templateId || isRequestPending) return;
  const version = conversationVersion;
  let fieldErrors = {};
  try {
    const result = await formJson("/validate", {
      template_id: formFlow.templateId,
      fields: formFlow.fields,
    });
    if (version !== conversationVersion) return;
    fieldErrors = result.errors || {};
  } catch (error) {
    if (version !== conversationVersion) return;
    const template = catalogTemplate(formFlow.templateId);
    (template?.required_fields || [])
      .filter((key) => !String(formFlow.fields?.[key] || "").trim())
      .forEach((key) => {
        fieldErrors[key] = "Trường này là bắt buộc.";
      });
  }
  if (Object.keys(fieldErrors).length) showMissingInfoPrompt(fieldErrors);
  else startFormInterview(true);
}

// allowIncomplete: người dân chọn "Tạo đơn dù còn thiếu" → tải đơn ngay, các ô
// còn thiếu để trống cho họ tự điền tay.
async function generateFormDocument({ allowIncomplete = false } = {}) {
  if (!formFlow?.templateId || isRequestPending) return;
  const version = conversationVersion;
  isRequestPending = true;
  updateControls();
  showTypingIndicator();
  try {
    const response = await fetch(`${FORM_API}/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        template_id: formFlow.templateId,
        fields: formFlow.fields,
        allow_incomplete: allowIncomplete,
      }),
    });
    if (version !== conversationVersion) return;
    if (!response.ok) {
      const result = await response.json();
      if (version !== conversationVersion) return;
      const fieldErrors = result.errors || {};
      hideTypingIndicator();
      if (Object.keys(fieldErrors).length) {
        showMissingInfoPrompt(fieldErrors);
        return;
      }
      throw new Error(result.error || `HTTP ${response.status}`);
    }
    const blob = await response.blob();
    if (version !== conversationVersion) return;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    const disposition = response.headers.get("Content-Disposition") || "";
    // Flask gửi tên tệp không có dấu ngoặc kép (filename=ten-don.docx).
    const nameMatch = disposition.match(/filename="?([^";]+)"?/);
    link.download = nameMatch
      ? nameMatch[1].trim()
      : `${formFlow.templateId || "don_hanh_chinh"}.docx`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    hideTypingIndicator();
    // Đơn đã tải xong: giấy tờ đính kèm sau đó được hỏi đáp như tài liệu mới,
    // không ghi đè vào đơn này (các nút của đơn ở trên vẫn dùng được).
    formFlow.stage = "done";
    renderMessage({
      content: allowIncomplete
        ? "Đơn Word đã được tạo và tải xuống. Các ô còn thiếu thông tin đang để trống, Anh/Chị điền tay vào đơn rồi in và ký."
        : "Đơn Word đã được tạo và tải xuống. Bạn hãy mở file, kiểm tra lần cuối rồi in và ký.",
    });
  } catch (error) {
    if (version !== conversationVersion) return;
    hideTypingIndicator();
    renderMessage({ content: `Chưa thể tạo đơn: ${error.message}` });
  } finally {
    if (version === conversationVersion) {
      isRequestPending = false;
      updateControls();
    }
  }
}

function parseSuggestions(json) {
  if (!json) return [];
  try {
    const list = JSON.parse(json);
    return Array.isArray(list) ? list : [];
  } catch (error) {
    return [];
  }
}

function createWelcomeChip({ label, question, icon }) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "chip-btn welcome-chip";
  button.title = question;
  button.innerHTML =
    `<svg class="welcome-chip-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${WELCOME_ICONS[icon] || ""}</svg>` +
    `<span class="welcome-chip-label"></span>${CHEVRON_ICON}`;
  button.querySelector(".welcome-chip-label").textContent = label;
  button.disabled = isRequestPending || isClearing;
  button.addEventListener("click", () => sendMessage(question));
  return button;
}

function resetToWelcome() {
  chatMessages.replaceChildren();
  const welcome = renderMessage({
    content:
      "Xin chào! Tôi là Trợ lý hành chính công của phường Minh Phụng.\n\n" +
      "Tôi có thể hỗ trợ tra cứu thủ tục hành chính, hướng dẫn hồ sơ, biểu mẫu, dịch vụ công và thông tin về thời hạn giải quyết. Anh/Chị cần hỗ trợ vấn đề gì ạ?",
    scroll: false,
  });
  const message = welcome.closest(".message");
  message.classList.add("welcome");
  message.querySelector(":scope > .timestamp")?.remove();
  const chips = document.createElement("div");
  chips.className = "welcome-chips";
  WELCOME_QUESTIONS.forEach((item) =>
    chips.appendChild(createWelcomeChip(item)),
  );
  message.appendChild(chips);
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

async function sendMessage(message, { fromVoice = false } = {}) {
  const question = String(message || "").trim();
  if (!question || isRequestPending || isClearing) return false;
  if (formFlow?.stage === "interview") return answerFormInterview(question);
  if (handleFormConversationInput(question)) return true;
  if (pendingDocumentContext && !documentAiConsent) {
    renderMessage({ content: question, isUser: true });
    renderMessage({
      content:
        "Vui lòng chọn **Đồng ý và hỏi về tài liệu** trước khi gửi nội dung tài liệu tới AI.",
    });
    return false;
  }
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
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: question,
        history: chatHistory,
        document_context: documentAiConsent ? pendingDocumentContext : "",
        document_ai_consent: documentAiConsent,
      }),
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    // Câu trả lời được stream theo từng dòng: hiện ngay dòng nào tới trước,
    // không chờ AI viết xong cả câu trả lời.
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let reply = "";
    let contentElement = null;
    for (;;) {
      const { done, value } = await reader.read();
      if (version !== conversationVersion) return false;
      if (done) break;
      reply += decoder.decode(value, { stream: true });
      if (!contentElement) {
        hideTypingIndicator();
        contentElement = renderMessage({ content: "" });
      }
      contentElement.innerHTML = formatMessage(
        reply.split(SUGGESTIONS_SEPARATOR)[0],
      );
      scrollToLatest();
    }
    reply += decoder.decode();
    const [answerText, suggestionsJson] = reply.split(SUGGESTIONS_SEPARATOR);
    reply = answerText.trim();
    if (!reply) throw new Error("Câu trả lời rỗng");
    contentElement.innerHTML = formatMessage(reply);
    appendSuggestions(contentElement, parseSuggestions(suggestionsJson));
    scrollToLatest();
    const context = response.headers.get("X-Context");
    if (context === "reset") chatHistory = [];
    else if (context === "append")
      chatHistory = [
        ...chatHistory,
        { role: "user", content: question },
        { role: "assistant", content: reply },
      ].slice(-MAX_HISTORY_MESSAGES);
    pendingDocumentContext = "";
    documentAiConsent = false;
    textInput.placeholder = DEFAULT_PLACEHOLDER;
    isRequestPending = false;
    updateControls();
    if (!reply.includes("Xin chào") && !reply.includes("Tạm biệt"))
      speak(reply);
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

function clearConversation() {
  if (isClearing) return;
  isClearing = true;
  conversationVersion += 1;
  activeRequest?.abort();
  activeRequest = null;
  stopRecognition();
  stopSpeaking();
  isRequestPending = false;
  chatHistory = [];
  discardFormInterview();
  formFlow = null;
  textInput.placeholder = DEFAULT_PLACEHOLDER;
  pendingDocumentContext = "";
  documentAiConsent = false;
  textInput.value = "";
  hideTranscript();
  resetToWelcome();
  setState("ready");
  isClearing = false;
  updateControls();
  textInput.focus();
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
    ? "Nhấn nút micro màu đỏ, cho phép truy cập rồi nói câu hỏi."
    : "Dùng trình duyệt hỗ trợ micro hoặc nhập tin nhắn để tra cứu.";
  composerStatus?.classList.add("is-notice");
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
document.getElementById("youtubeBtn")?.addEventListener("click", () => {
  window.open(
    "https://www.youtube.com/@TTPVHCCMinhPh%E1%BB%A5ng",
    "_blank",
    "noopener",
  );
});
document.getElementById("khuPhoBtn")?.addEventListener("click", () => {
  window.open(
    "https://sites.google.com/view/phuongminhphung/trang-ch%E1%BB%A7?authuser=0",
    "_blank",
    "noopener",
  );
});
attachBtn?.addEventListener("click", () => formFileInput?.click());
// Mở trợ lý điền đơn (chọn mẫu → hỏi đáp hoặc đọc giấy tờ → tải file Word).
// Bấm giữa chừng một lượt điền đơn khác thì bắt đầu lại từ bước chọn mẫu.
dienDonBtn?.addEventListener("click", () => {
  if (isRequestPending || isClearing) return;
  stopRecognition();
  renderMessage({ content: "Tôi muốn điền đơn tự động", isUser: true });
  startFormAssistant();
});
formFileInput?.addEventListener("change", () =>
  processDocumentFiles(formFileInput),
);

resetToWelcome();
updateAudioToggle();
updateControls();
setState(recognition ? "ready" : "unsupported");
// Tải sẵn danh mục mẫu đơn để nhận ra ngay câu như "làm đơn mừng thọ"; nếu
// lỗi thì startFormAssistant sẽ thử tải lại khi người dân bấm "Điền đơn".
loadFormCatalog().catch(() => {});
