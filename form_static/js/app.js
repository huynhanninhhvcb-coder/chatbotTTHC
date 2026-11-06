// Logic chính của giao diện: tải ảnh/PDF lên để trích xuất thông tin, phỏng vấn bằng giọng nói/bàn phím
// để thu thập dữ liệu, hiển thị và cho chỉnh sửa kết quả ở bước "review", rồi tạo file .docx để tải về.
(() => {
  const apiUrl = (path) => `${window.location.pathname.replace(/\/$/, '')}/api${path}`;
  // Lấy tham chiếu tới các phần tử DOM chính của trang (form tải lên, form dữ liệu, khu vực phỏng vấn giọng nói...).
  const uploadForm = document.querySelector('#upload-form');
  const dataForm = document.querySelector('#data-form');
  const fileInput = document.querySelector('#file-input');
  const fileName = document.querySelector('#file-name');
  const dropZone = document.querySelector('#drop-zone');
  const selectedFilePanel = document.querySelector('#selected-file-panel');
  const fileSelectionSummary = document.querySelector('#file-selection-summary');
  const fileList = document.querySelector('#file-list');
  const clearFilesButton = document.querySelector('#clear-files-button');
  const review = document.querySelector('#review-section');
  const status = document.querySelector('#status');
  const templateSelect = document.querySelector('#template-id');
  const useAi = document.querySelector('#use-ai');
  const scanPanel = document.querySelector('#scan-panel');
  const interviewPanel = document.querySelector('#interview-panel');
  const methodButtons = document.querySelectorAll('[data-method]');
  const interviewStart = document.querySelector('#interview-start');
  const interviewForm = document.querySelector('#interview-form');
  const interviewAnswer = document.querySelector('#interview-answer');
  const interviewChat = document.querySelector('#interview-chat');
  const interviewProgress = document.querySelector('#interview-progress');
  const interviewSend = document.querySelector('#interview-send');
  const voiceControls = document.querySelector('#voice-controls');
  const voiceAnswerButton = document.querySelector('#voice-answer-button');
  const repeatQuestionButton = document.querySelector('#repeat-question-button');
  const voiceStatus = document.querySelector('#voice-status');
  const voiceOutputStatus = document.querySelector('#voice-output-status');
  const voiceTranscript = document.querySelector('#voice-transcript');
  const voiceConfirmControls = document.querySelector('#voice-confirm-controls');
  const voiceConfirmButton = document.querySelector('#voice-confirm-button');
  const voiceRetryButton = document.querySelector('#voice-retry-button');
  const keyboardFallback = document.querySelector('#keyboard-fallback');
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const voiceSupported = Boolean(SpeechRecognition);
  const speechOutputSupported = Boolean(window.speechSynthesis && window.SpeechSynthesisUtterance);
  const MAX_UPLOAD_FILES = 5;
  const MAX_FILE_SIZE_BYTES = 12 * 1024 * 1024;
  const MAX_TOTAL_UPLOAD_BYTES = MAX_UPLOAD_FILES * MAX_FILE_SIZE_BYTES;
  let interviewSessionId = null;
  let currentQuestion = '';
  let pendingVoiceAnswer = '';
  let recognition = null;
  let listening = false;
  let finalVoiceTranscript = [];
  let voiceAnswerSubmitted = false;
  let activeRecognitionSessionId = null;
  let pendingRecognitionSessionId = null;
  let suppressRecognitionSubmission = false;
  let recognitionHadError = false;
  let speechSequence = 0;
  let selectedUploadFiles = [];
  let uploadInProgress = false;
  let dragDepth = 0;

  // Hiển thị dòng thông báo trạng thái chung của trang (thành công/lỗi/thông tin).
  const showStatus = (message, kind = 'info') => {
    status.textContent = message;
    status.className = `status ${kind}`;
    status.hidden = false;
  };

  // Hiển thị dòng trạng thái riêng cho tính năng đọc câu hỏi bằng giọng nói (vd: đang tìm giọng đọc tiếng Việt).
  const setVoiceOutputStatus = (message, kind = '') => {
    if (!voiceOutputStatus) return;
    voiceOutputStatus.textContent = message;
    voiceOutputStatus.className = `voice-output-status ${kind}`.trim();
  };

  // Tìm giọng đọc tiếng Việt phù hợp nhất trong danh sách giọng đọc mà trình duyệt cung cấp
  // (ưu tiên mã ngôn ngữ vi-VN, rồi tới các biến thể vi-*, sau đó mới xét theo tên giọng đọc).
  const getVietnameseVoice = () => {
    if (!speechOutputSupported) return null;
    const voices = window.speechSynthesis.getVoices();
    const languageOf = (voice) => (voice.lang || '').toLowerCase().replace('_', '-');
    const nameLooksVietnamese = (voice) => /vietnamese|tiếng việt|tieng viet/i.test(voice.name || '');

    return voices.find((voice) => languageOf(voice) === 'vi-vn')
      || voices.find((voice) => languageOf(voice).startsWith('vi-'))
      || voices.find((voice) => languageOf(voice) === 'vi')
      || voices.find(nameLooksVietnamese)
      || null;
  };

  // Cập nhật dòng trạng thái giọng đọc tùy theo trình duyệt có hỗ trợ đọc, đã tải xong
  // danh sách giọng đọc hay chưa, và có tìm thấy giọng tiếng Việt hay không. Trả về giọng tìm được (hoặc null).
  const updateVietnameseVoiceStatus = () => {
    if (!speechOutputSupported) {
      setVoiceOutputStatus('Trình duyệt không hỗ trợ đọc câu hỏi. Câu hỏi vẫn hiển thị trên màn hình để bạn trả lời bằng micro.', 'error');
      return null;
    }

    const voice = getVietnameseVoice();
    if (voice) {
      setVoiceOutputStatus(`Giọng đọc tiếng Việt đang dùng: ${voice.name} (${voice.lang || 'vi-VN'}).`, 'success');
      return voice;
    }

    if (!window.speechSynthesis.getVoices().length) {
      setVoiceOutputStatus('Đang tải danh sách giọng đọc tiếng Việt…');
      return null;
    }

    setVoiceOutputStatus('Chưa tìm thấy giọng đọc tiếng Việt trên thiết bị. Câu hỏi vẫn hiển thị để bạn trả lời bằng micro. Trên Windows, hãy vào Cài đặt > Giọng nói hoặc Trình tường thuật > Thêm giọng nói, cài Tiếng Việt, rồi khởi động lại Chrome/Edge.', 'error');
    return null;
  };

  // Dừng ngay việc đọc câu hỏi hiện tại; tăng speechSequence để mọi callback đọc
  // dở dang (bất đồng bộ) tự nhận ra mình đã lỗi thời và không tiếp tục nữa.
  const stopSpeaking = () => {
    speechSequence += 1;
    if (speechOutputSupported) window.speechSynthesis.cancel();
  };

  // Bật/tắt trạng thái "đang xử lý" của một nút: vô hiệu hóa nút và đổi nhãn hiển thị.
  const setBusy = (button, busy, text) => {
    button.disabled = busy;
    button.querySelector('.button-label').textContent = text;
  };

  // Định dạng dung lượng tệp để hiển thị (KB nếu dưới 1 MB, ngược lại tính theo MB).
  const formatFileSize = (bytes) => {
    if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  // Khóa định danh duy nhất cho một tệp (tên + kích thước + thời gian sửa đổi), dùng để phát hiện tệp trùng lặp.
  const fileKey = (file) => `${file.name}::${file.size}::${file.lastModified}`;

  // Chỉ cho phép chọn tệp PDF hoặc ảnh (PNG/JPG/JPEG) dựa theo phần mở rộng tên tệp.
  const isAllowedUploadFile = (file) => /\.(pdf|png|jpe?g)$/i.test(file.name || '');

  // Đồng bộ lại thuộc tính `files` của input[type=file] theo mảng selectedUploadFiles đang quản lý trong JS,
  // vì input file gốc không cho phép gán trực tiếp một mảng File nên phải dựng qua DataTransfer.
  const syncFileInput = () => {
    const transfer = new DataTransfer();
    selectedUploadFiles.forEach((file) => transfer.items.add(file));
    fileInput.files = transfer.files;
  };

  // Vẽ lại danh sách tệp đã chọn (tên, loại, dung lượng, nút "Bỏ") dựa trên mảng selectedUploadFiles hiện tại.
  const renderSelectedFiles = () => {
    const totalBytes = selectedUploadFiles.reduce((sum, file) => sum + file.size, 0);
    fileList.replaceChildren();

    if (!selectedUploadFiles.length) {
      fileName.textContent = 'Chọn một hoặc nhiều ảnh/PDF';
      selectedFilePanel.hidden = true;
      return;
    }

    fileName.textContent = selectedUploadFiles.length === 1
      ? selectedUploadFiles[0].name
      : `Đã chọn ${selectedUploadFiles.length} tệp`;
    fileSelectionSummary.textContent = `${selectedUploadFiles.length}/${MAX_UPLOAD_FILES} tệp · ${formatFileSize(totalBytes)}`;
    selectedUploadFiles.forEach((file, index) => {
      const item = document.createElement('li');
      item.className = 'file-list-item';
      const details = document.createElement('span');
      details.className = 'file-list-details';
      const type = document.createElement('span');
      type.className = 'file-type-badge';
      type.textContent = /\.pdf$/i.test(file.name) ? 'PDF' : 'Ảnh';
      const name = document.createElement('span');
      name.className = 'file-list-name';
      name.textContent = file.name;
      const size = document.createElement('span');
      size.className = 'file-list-size';
      size.textContent = formatFileSize(file.size);
      details.append(type, name, size);

      const removeButton = document.createElement('button');
      removeButton.type = 'button';
      removeButton.className = 'file-remove-button';
      removeButton.dataset.fileIndex = String(index);
      removeButton.textContent = 'Bỏ';
      removeButton.disabled = uploadInProgress;
      item.append(details, removeButton);
      fileList.append(item);
    });

    clearFilesButton.disabled = uploadInProgress;
    selectedFilePanel.hidden = false;
  };

  // Thêm các tệp mới được chọn (từ input file hoặc kéo-thả) vào danh sách đang chờ tải lên,
  // kiểm tra từng tệp theo định dạng/kích thước/trùng lặp/giới hạn số lượng và tổng dung lượng,
  // rồi báo lỗi (nếu có) cho những tệp bị từ chối.
  const addUploadFiles = (files) => {
    if (uploadInProgress) return;
    const nextFiles = [...selectedUploadFiles];
    const knownFiles = new Set(nextFiles.map(fileKey));
    let totalBytes = nextFiles.reduce((sum, file) => sum + file.size, 0);
    const rejected = [];

    Array.from(files).forEach((file) => {
      if (!isAllowedUploadFile(file)) {
        rejected.push(`${file.name}: chỉ nhận PDF, PNG, JPG hoặc JPEG.`);
      } else if (file.size > MAX_FILE_SIZE_BYTES) {
        rejected.push(`${file.name}: lớn hơn 12 MB.`);
      } else if (knownFiles.has(fileKey(file))) {
        rejected.push(`${file.name}: đã có trong danh sách.`);
      } else if (nextFiles.length >= MAX_UPLOAD_FILES) {
        rejected.push(`${file.name}: chỉ được chọn tối đa ${MAX_UPLOAD_FILES} tệp.`);
      } else if (totalBytes + file.size > MAX_TOTAL_UPLOAD_BYTES) {
        rejected.push(`${file.name}: vượt dung lượng tối đa của một lượt tải lên.`);
      } else {
        nextFiles.push(file);
        knownFiles.add(fileKey(file));
        totalBytes += file.size;
      }
    });

    selectedUploadFiles = nextFiles;
    syncFileInput();
    renderSelectedFiles();
    if (rejected.length) showStatus(rejected.join(' '), 'error');
  };

  // Xóa toàn bộ tệp đang chờ tải lên (bấm nút "Xóa hết").
  const clearUploadFiles = () => {
    if (uploadInProgress) return;
    selectedUploadFiles = [];
    syncFileInput();
    renderSelectedFiles();
  };

  // Xóa trắng toàn bộ giá trị các trường trong form dữ liệu và bỏ đánh dấu "cần rà soát".
  const clearFieldValues = () => {
    dataForm.querySelectorAll('input, select').forEach((input) => {
      input.value = '';
      input.closest('.input-group')?.classList.remove('needs-review');
    });
  };

  // Điền giá trị vào các trường của form dữ liệu theo kết quả trích xuất/phỏng vấn trả về.
  // `replace = true` sẽ xóa trắng form trước khi điền; các trường có độ tin cậy "medium"
  // sẽ được đánh dấu "needs-review" để nhắc người dùng kiểm tra lại.
  const fillFields = (fields, confidence = {}, replace = false) => {
    if (replace) {
      clearFieldValues();
      Object.keys(knownValues).forEach((name) => delete knownValues[name]);
    }
    Object.entries(fields).forEach(([name, value]) => {
      const input = dataForm.elements.namedItem(name);
      if (value) knownValues[name] = value;
      if (input && value) input.value = value;
      if (input && confidence[name] === 'medium') input.closest('.input-group')?.classList.add('needs-review');
    });
  };

  // Đánh dấu "cần rà soát" cho các trường mà backend phát hiện có giá trị mâu thuẫn giữa các tệp/nguồn dữ liệu.
  const markConflictingFields = (conflicts = []) => {
    conflicts.forEach(({ field }) => {
      dataForm.elements.namedItem(field)?.closest('.input-group')?.classList.add('needs-review');
    });
  };

  // Xóa mọi trạng thái lỗi validate đang hiển thị trên form (viền đỏ + thông báo lỗi dưới mỗi trường).
  const clearErrors = () => {
    dataForm.querySelectorAll('.invalid').forEach((element) => element.classList.remove('invalid'));
    dataForm.querySelectorAll('.field-error').forEach((element) => element.remove());
  };

  // Hiển thị lỗi validate trả về từ backend: mở khối <details> chứa trường lỗi, tô viền đỏ
  // và chèn thông báo lỗi ngay dưới trường tương ứng.
  const renderErrors = (errors) => {
    clearErrors();
    Object.entries(errors || {}).forEach(([name, message]) => {
      const input = dataForm.elements.namedItem(name);
      if (!input) return;
      const group = input.closest('.input-group');
      input.closest('details')?.setAttribute('open', '');
      input.classList.add('invalid');
      const error = document.createElement('small');
      error.className = 'field-error';
      error.textContent = message;
      group.append(error);
    });
  };

  // Bỏ khoảng trắng, dấu chấm, dấu gạch ngang trong số tài khoản ngân hàng.
  const normalizeBankAccountNumber = (value) => value.trim().replace(/[\s.-]/g, '');

  // Gom toàn bộ giá trị các trường trong form dữ liệu thành một object để gửi lên API validate/generate.
  const fieldsPayload = () => {
    const fields = Object.fromEntries(new FormData(dataForm).entries());
    const accountNumber = fields.bank_account_number || '';
    // Chỉ bỏ dấu cách/dấu phân tách khi nội dung vẫn hoàn toàn là chữ số.
    // Nếu có ký tự lạ, giữ nguyên để API báo lỗi thay vì âm thầm sửa sai.
    if (/^[\d\s.-]+$/.test(accountNumber)) {
      fields.bank_account_number = normalizeBankAccountNumber(accountNumber);
    }
    return fields;
  };

  // Chuyển đổi giữa 2 phương thức nhập liệu: "scan" (tải ảnh/PDF lên) và phỏng vấn (voice/keyboard).
  // Khi chuyển sang scan thì dừng mọi giọng đọc/ghi âm đang chạy của phần phỏng vấn.
  const setMethod = (method) => {
    const isScan = method === 'scan';
    if (isScan) {
      stopSpeaking();
      stopVoiceCapture();
    }
    scanPanel.hidden = !isScan;
    interviewPanel.hidden = isScan;
    methodButtons.forEach((button) => {
      const selected = button.dataset.method === method;
      button.classList.toggle('active', selected);
      button.setAttribute('aria-selected', String(selected));
    });
  };

  const templateFieldsContainer = document.querySelector('#template-fields');
  // Giá trị đã nhập/trích xuất, giữ lại khi đổi mẫu để không phải nhập lại.
  const knownValues = {};
  let formCatalog = null;
  const WIDE_FIELD = /address|full_name|category|content|place|issuer|procedure|ward|facility|org_name/;
  const NUMERIC_FIELD = /citizen_id$|^bank_account_number$|^social_insurance_number$|^ewallet_number$/;

  const loadCatalog = async () => {
    if (!formCatalog) {
      const response = await fetch(apiUrl('/templates'));
      if (!response.ok) throw new Error('Không tải được danh sách trường của mẫu đơn.');
      formCatalog = await response.json();
    }
    return formCatalog;
  };

  const rememberValues = () => {
    dataForm.querySelectorAll('input, select').forEach((input) => {
      if (input.value) knownValues[input.name] = input.value;
      else delete knownValues[input.name];
    });
  };

  // Dựng các ô nhập theo đúng danh sách trường của mẫu đang chọn (nhãn, ô bắt
  // buộc, danh sách lựa chọn đều lấy từ máy chủ nên luôn khớp với tệp Word).
  const setTemplateFields = async (templateId) => {
    rememberValues();
    let catalog;
    try {
      catalog = await loadCatalog();
    } catch (error) {
      showStatus(error.message, 'error');
      return;
    }
    const template = catalog.templates.find((item) => item.id === templateId);
    if (!template) return;
    const required = new Set(template.required_fields);
    const groups = template.fields.map(({ name, label }) => {
      const group = document.createElement('label');
      group.className = `input-group${WIDE_FIELD.test(name) ? ' wide' : ''}`;
      group.append(label);
      if (required.has(name)) {
        const mark = document.createElement('em');
        mark.textContent = '*';
        group.append(' ', mark);
      }
      let input;
      const options = catalog.options[name];
      if (options) {
        input = document.createElement('select');
        input.append(new Option('-- Chọn --', ''), ...options.map((value) => new Option(value, value)));
      } else {
        input = document.createElement('input');
        if (/date/.test(name)) {
          input.placeholder = 'dd/mm/yyyy';
          input.inputMode = 'numeric';
        } else if (/phone/.test(name)) {
          input.inputMode = 'tel';
        } else if (NUMERIC_FIELD.test(name)) {
          input.inputMode = 'numeric';
        }
      }
      input.name = name;
      input.required = required.has(name);
      input.value = knownValues[name] || '';
      group.append(input);
      return group;
    });
    templateFieldsContainer.replaceChildren(...groups);
  };

  // Thêm một dòng tin nhắn vào khung chat phỏng vấn (role: 'citizen' người dân hoặc 'assistant' trợ lý) và cuộn xuống cuối.
  const addChatMessage = (role, message) => {
    if (!message) return;
    const messageElement = document.createElement('div');
    messageElement.className = `chat-message ${role}`;
    messageElement.textContent = message;
    interviewChat.append(messageElement);
    interviewChat.scrollTop = interviewChat.scrollHeight;
  };

  // Hiển thị tiến độ phỏng vấn dạng "Câu hỏi X/Y".
  const showInterviewProgress = (progress) => {
    interviewProgress.textContent = `Câu hỏi ${progress.current}/${progress.total}`;
    interviewProgress.hidden = false;
  };

  // Cập nhật dòng trạng thái của khu vực trả lời bằng giọng nói (vd: đang nghe, đang lỗi...).
  const setVoiceStatus = (message, kind = '') => {
    voiceStatus.textContent = message;
    voiceStatus.className = `voice-status ${kind}`.trim();
  };

  // Bật/tắt trạng thái hiển thị của nút micro (đang lắng nghe hay không) và cập nhật nhãn nút tương ứng.
  const setVoiceListening = (active) => {
    listening = active;
    voiceAnswerButton.disabled = false;
    voiceAnswerButton.classList.toggle('listening', active);
    voiceAnswerButton.setAttribute('aria-pressed', String(active));
    voiceAnswerButton.querySelector('.button-label').textContent = active
      ? 'Đang lắng nghe… Bấm để dừng'
      : 'Bấm để trả lời bằng giọng nói';
  };

  // Dừng ghi âm giọng nói đang diễn ra (hoặc sắp bắt đầu) và đảm bảo kết quả nhận dạng dở dang
  // sẽ không bị gửi đi (đánh dấu suppressRecognitionSubmission/voiceAnswerSubmitted trước khi abort).
  const stopVoiceCapture = () => {
    suppressRecognitionSubmission = true;
    finalVoiceTranscript = [];
    voiceAnswerSubmitted = true;
    recognitionHadError = false;
    const recognitionWasStartingOrListening = Boolean(
      listening || pendingRecognitionSessionId || activeRecognitionSessionId,
    );
    activeRecognitionSessionId = null;
    pendingRecognitionSessionId = null;
    if (recognition && recognitionWasStartingOrListening) {
      try {
        recognition.abort();
      } catch (_error) {
        // Trình duyệt có thể đã tự dừng phiên nhận dạng.
      }
    }
    setVoiceListening(false);
  };

  // Đọc to một đoạn văn bản (câu hỏi phỏng vấn) bằng giọng tiếng Việt qua Web Speech API.
  // `sequence` dùng để chống đọc chồng chéo: nếu có yêu cầu đọc mới hơn phát sinh trong lúc
  // đang chờ tải danh sách giọng đọc, yêu cầu cũ (sequence khác speechSequence) sẽ tự bỏ qua.
  // Nếu danh sách giọng đọc của trình duyệt chưa sẵn sàng, hàm tự thử lại tối đa 8 lần, mỗi lần cách nhau 250ms.
  const speak = (text, attempt = 0, requestedSequence = null) => {
    if (!text) return;
    const sequence = requestedSequence ?? ++speechSequence;
    if (!speechOutputSupported) {
      updateVietnameseVoiceStatus();
      return;
    }

    const voice = getVietnameseVoice();
    if (!voice) {
      if (!window.speechSynthesis.getVoices().length && attempt < 8) {
        updateVietnameseVoiceStatus();
        window.setTimeout(() => {
          if (sequence === speechSequence) speak(text, attempt + 1, sequence);
        }, 250);
        return;
      }
      if (sequence === speechSequence) {
        updateVietnameseVoiceStatus();
        setVoiceStatus('Câu hỏi đang hiển thị trên màn hình. Bấm micro để trả lời bằng tiếng Việt.');
      }
      return;
    }

    if (sequence !== speechSequence) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.voice = voice;
    utterance.lang = voice.lang || 'vi-VN';
    utterance.rate = 0.92;
    updateVietnameseVoiceStatus();
    utterance.onstart = () => {
      if (sequence === speechSequence) setVoiceStatus('Trợ lý đang đọc câu hỏi bằng tiếng Việt…');
    };
    utterance.onend = () => {
      if (sequence === speechSequence) setVoiceStatus('Bấm nút micro và trả lời bằng giọng nói.');
    };
    utterance.onerror = (event) => {
      if (sequence !== speechSequence || ['canceled', 'interrupted'].includes(event.error)) return;
      setVoiceStatus('Không thể đọc câu hỏi thành tiếng. Bạn vẫn có thể bấm micro để trả lời.', 'error');
    };
    window.speechSynthesis.speak(utterance);
  };

  // Danh sách giọng đọc của trình duyệt thường tải bất đồng bộ; khi tải xong (sự kiện 'voiceschanged'),
  // cập nhật lại dòng trạng thái để phản ánh việc đã/chưa tìm thấy giọng tiếng Việt.
  if (speechOutputSupported) {
    window.speechSynthesis.addEventListener('voiceschanged', () => {
      updateVietnameseVoiceStatus();
    });
  }

  // Hàm dùng chung để gọi các API dạng JSON (POST + body JSON), trả về dữ liệu JSON của response
  // hoặc ném lỗi (kèm thông báo lỗi từ backend) nếu response không thành công.
  const jsonRequest = async (url, payload) => {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail ? `${result.error} ${result.detail}` : result.error);
    return result;
  };

  // Kết thúc phiên phỏng vấn: điền dữ liệu thu thập được vào form, ẩn các khối điều khiển giọng nói,
  // hiện khu vực review để người dùng rà soát lần cuối trước khi tạo đơn.
  const finishInterview = (result) => {
    stopSpeaking();
    fillFields(result.fields, {}, true);
    voiceControls.hidden = true;
    voiceConfirmControls.hidden = true;
    keyboardFallback.hidden = true;
    review.hidden = false;
    review.scrollIntoView({ behavior: 'smooth', block: 'start' });
    showStatus('Đã tự điền thông tin từ cuộc phỏng vấn. Vui lòng rà soát lần cuối.', 'success');
  };

  // Gửi một câu trả lời (do gõ hoặc do nói) của người dân lên API phỏng vấn, hiển thị câu trả lời
  // trong khung chat, rồi xử lý phản hồi: nếu phỏng vấn hoàn tất thì gọi finishInterview, ngược lại
  // hiển thị câu hỏi tiếp theo và đọc to câu hỏi đó.
  const submitInterviewAnswer = async (answer) => {
    if (!answer || !interviewSessionId) return;
    pendingVoiceAnswer = '';
    voiceConfirmControls.hidden = true;
    addChatMessage('citizen', answer);
    interviewAnswer.value = '';
    setBusy(interviewSend, true, 'Đang kiểm tra…');
    if (voiceSupported) setBusy(voiceAnswerButton, true, 'Đang kiểm tra…');
    try {
      // Gọi API trả lời câu hỏi phỏng vấn, backend kiểm tra câu trả lời và trả về câu hỏi kế tiếp
      // (hoặc thông báo hoàn tất phỏng vấn kèm toàn bộ dữ liệu đã thu thập).
      const result = await jsonRequest(apiUrl('/interview/answer'), { session_id: interviewSessionId, answer });
      addChatMessage('assistant', result.message);
      showInterviewProgress(result.progress);
      if (result.complete) {
        finishInterview(result);
        return;
      }
      currentQuestion = result.question || currentQuestion;
      if (result.error && result.question) addChatMessage('assistant', result.question);
      speak(result.error ? `${result.message}. ${result.question}` : result.question);
      interviewAnswer.focus();
    } catch (error) {
      showStatus(error.message || 'Phiên phỏng vấn gặp lỗi. Hãy bắt đầu lại.', 'error');
      if (error.message?.includes('không còn hiệu lực')) {
        interviewSessionId = null;
        voiceControls.hidden = true;
        keyboardFallback.hidden = true;
      }
    } finally {
      setBusy(interviewSend, false, 'Gửi');
      if (voiceSupported && !listening) setBusy(voiceAnswerButton, false, 'Bấm để trả lời bằng giọng nói');
    }
  };

  // Xử lý bản chép lời (transcript) sau khi nhận dạng giọng nói kết thúc: hiển thị lại cho người dân xem
  // và chờ xác nhận trước khi thực sự gửi đi (không tự động gửi để tránh gửi nhầm do nhận dạng sai).
  const handleRecognizedVoice = (transcript) => {
    // Không tự gửi bất kỳ câu trả lời bằng giọng nói nào: người dân phải xác nhận trước.
    pendingVoiceAnswer = transcript;
    voiceTranscript.hidden = false;
    voiceTranscript.textContent = `Đã nghe: ${transcript}`;
    voiceConfirmControls.hidden = false;
    setVoiceStatus('Hãy xác nhận bản chép lời. Nếu chưa đúng, chọn “Nói lại”.');
    voiceConfirmButton.focus();
  };

  // Khởi tạo (một lần) đối tượng SpeechRecognition dùng cho phần trả lời phỏng vấn bằng giọng nói,
  // và gắn các trình xử lý sự kiện bắt đầu/có kết quả/có lỗi/kết thúc ghi âm.
  const initialiseRecognition = () => {
    if (!voiceSupported || recognition) return;
    recognition = new SpeechRecognition();
    recognition.lang = 'vi-VN';
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    // Bắt đầu ghi âm: chỉ tiếp tục nếu đây đúng là phiên đang chờ và vẫn thuộc phiên phỏng vấn hiện tại
    // (tránh trường hợp trình duyệt khởi động micro trễ, sau khi người dùng đã hủy hoặc đổi phiên khác).
    recognition.onstart = () => {
      activeRecognitionSessionId = pendingRecognitionSessionId;
      pendingRecognitionSessionId = null;
      if (!activeRecognitionSessionId || activeRecognitionSessionId !== interviewSessionId || suppressRecognitionSubmission) {
        try {
          recognition.abort();
        } catch (_error) {
          // Phiên nhận dạng đã dừng trước khi micro được mở.
        }
        return;
      }
      setVoiceListening(true);
      setVoiceStatus('Đang lắng nghe. Hãy nói câu trả lời của bạn.');
      voiceTranscript.hidden = false;
      voiceTranscript.textContent = '';
    };
    // Có kết quả nhận dạng (tạm thời hoặc cuối cùng): gộp các đoạn đã chốt (isFinal) và hiển thị
    // bản chép lời cập nhật liên tục lên màn hình để người dùng theo dõi.
    recognition.onresult = (event) => {
      const transcriptParts = [];
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const part = event.results[index][0].transcript.trim();
        transcriptParts.push(part);
        if (event.results[index].isFinal && part) finalVoiceTranscript.push(part);
      }
      voiceTranscript.textContent = `Đã nghe: ${finalVoiceTranscript.join(' ') || transcriptParts.join(' ')}`;
    };
    // Có lỗi khi nhận dạng: bỏ qua lỗi "aborted" (do chính mình chủ động hủy), các lỗi khác
    // hiển thị thông báo phù hợp và mở phương án nhập bằng bàn phím để không chặn người dùng.
    recognition.onerror = (event) => {
      if (event.error === 'aborted') return;
      recognitionHadError = true;
      suppressRecognitionSubmission = true;
      const messages = {
        'not-allowed': 'Trình duyệt chưa được cấp quyền dùng micro. Hãy cho phép micro rồi thử lại.',
        'no-speech': 'Tôi chưa nghe thấy câu trả lời. Hãy bấm micro và nói lại.',
        'network': 'Dịch vụ nhận dạng giọng nói của trình duyệt đang không khả dụng.',
        'audio-capture': 'Không tìm thấy micro. Hãy kiểm tra thiết bị âm thanh rồi thử lại.',
        'service-not-allowed': 'Dịch vụ nhận dạng giọng nói đang bị trình duyệt chặn.',
        'language-not-supported': 'Trình duyệt chưa hỗ trợ nhận dạng tiếng Việt. Hãy thử Chrome hoặc Edge mới nhất.',
      };
      setVoiceStatus(messages[event.error] || 'Không thể nhận dạng giọng nói. Bạn có thể thử lại hoặc dùng bàn phím.', 'error');
      keyboardFallback.hidden = false;
      keyboardFallback.open = true;
    };
    // Kết thúc ghi âm: chỉ tự động gửi bản chép lời nếu không bị hủy/không lỗi, vẫn đúng phiên phỏng vấn
    // hiện tại, có nội dung, và chưa từng gửi (tránh gửi trùng khi onend bị gọi nhiều lần).
    recognition.onend = () => {
      const transcript = finalVoiceTranscript.join(' ').trim();
      const sessionForRecognition = activeRecognitionSessionId;
      const discarded = suppressRecognitionSubmission;
      const canSubmit = !discarded
        && !recognitionHadError
        && sessionForRecognition === interviewSessionId
        && transcript
        && !voiceAnswerSubmitted;
      setVoiceListening(false);
      activeRecognitionSessionId = null;
      suppressRecognitionSubmission = false;
      if (canSubmit) {
        voiceAnswerSubmitted = true;
        handleRecognizedVoice(transcript);
      } else if (!recognitionHadError && !discarded) {
        setVoiceStatus('Bấm nút micro và trả lời bằng giọng nói.');
      }
    };
  };

  // Người dùng chọn tệp qua hộp thoại chọn tệp của trình duyệt.
  fileInput.addEventListener('change', () => addUploadFiles(fileInput.files));
  // Bấm nút xóa hết danh sách tệp đã chọn.
  clearFilesButton.addEventListener('click', clearUploadFiles);
  // Bấm nút "Bỏ" trên một dòng tệp cụ thể trong danh sách (dùng event delegation trên fileList).
  fileList.addEventListener('click', (event) => {
    const removeButton = event.target.closest('.file-remove-button');
    if (!removeButton || uploadInProgress) return;
    const index = Number(removeButton.dataset.fileIndex);
    if (!Number.isInteger(index)) return;
    selectedUploadFiles.splice(index, 1);
    syncFileInput();
    renderSelectedFiles();
  });
  // Bấm các nút chọn phương thức nhập liệu (quét ảnh/PDF hay phỏng vấn).
  methodButtons.forEach((button) => button.addEventListener('click', () => setMethod(button.dataset.method)));
  // Đổi loại mẫu đơn: cập nhật lại các nhóm trường hiển thị tương ứng.
  templateSelect.addEventListener('change', () => setTemplateFields(templateSelect.value));
  setTemplateFields(templateSelect.value);
  // Các sự kiện kéo-thả tệp vào vùng drop-zone: tô sáng vùng thả khi kéo tệp vào,
  // dùng dragDepth để đếm số lần dragenter/dragleave lồng nhau (vì các phần tử con cũng phát sinh sự kiện).
  dropZone.addEventListener('dragenter', (event) => {
    event.preventDefault();
    if (uploadInProgress) return;
    dragDepth += 1;
    dropZone.classList.add('dragging');
  });
  dropZone.addEventListener('dragover', (event) => {
    event.preventDefault();
    if (!uploadInProgress) dropZone.classList.add('dragging');
  });
  dropZone.addEventListener('dragleave', (event) => {
    event.preventDefault();
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) dropZone.classList.remove('dragging');
  });
  // Thả tệp vào vùng drop-zone: thêm các tệp đó vào danh sách chờ tải lên.
  dropZone.addEventListener('drop', (event) => {
    event.preventDefault();
    dragDepth = 0;
    dropZone.classList.remove('dragging');
    if (!uploadInProgress) addUploadFiles(event.dataTransfer.files);
  });

  // Xử lý khi người dùng bấm nút "Trích xuất thông tin": kiểm tra hợp lệ các tệp đã chọn,
  // gửi lên API /api/extract (kèm theo dõi tiến trình nếu tải nhiều tệp), rồi điền kết quả vào form review.
  uploadForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const files = [...selectedUploadFiles];
    if (!files.length) return showStatus('Hãy chọn ít nhất một ảnh hoặc tệp PDF trước.', 'error');
    if (files.length > MAX_UPLOAD_FILES) return showStatus(`Chỉ được tải lên tối đa ${MAX_UPLOAD_FILES} tệp.`, 'error');
    if (files.some((file) => file.size > MAX_FILE_SIZE_BYTES)) return showStatus('Mỗi tệp phải có dung lượng tối đa 12 MB.', 'error');
    if (files.reduce((sum, file) => sum + file.size, 0) > MAX_TOTAL_UPLOAD_BYTES) return showStatus('Tổng dung lượng tệp đã chọn vượt giới hạn.', 'error');

    const button = document.querySelector('#extract-button');
    uploadInProgress = true;
    renderSelectedFiles();
    const processingLabel = useAi?.checked
      ? files.length > 1
        ? `AI đang đọc ${files.length} tệp…`
        : 'AI đang đọc tài liệu…'
      : files.length > 1
        ? `Đang quét ${files.length} tệp…`
        : 'Đang trích xuất…';
    setBusy(button, true, processingLabel);
    status.hidden = true;
    clearErrors();
    // Theo dõi tiến trình chỉ có ý nghĩa khi có nhiều tệp: batch mới lưu
    // trạng thái theo batch_id ở backend, tệp đơn dùng đường xử lý khác
    // không cập nhật tiến trình này.
    let progressTimer = null;
    let batchId = null;
    if (files.length > 1) {
      // Sinh một batch_id ngẫu nhiên để backend gộp tiến trình xử lý nhiều tệp theo cùng một lượt tải lên.
      batchId = window.crypto?.randomUUID
        ? window.crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      // Định kỳ gọi API kiểm tra tiến trình xử lý batch để cập nhật nhãn nút (vd: "Đang xử lý ảnh 2/5…").
      progressTimer = window.setInterval(async () => {
        try {
          const response = await fetch(apiUrl(`/extract/progress/${batchId}`));
          if (!response.ok) return;
          const progress = await response.json();
          if (progress.total > 0) {
            setBusy(button, true, `Đang xử lý ảnh ${progress.done}/${progress.total}…`);
          }
        } catch {
          // Bỏ qua lỗi polling: chỉ ảnh hưởng dòng trạng thái, không ảnh hưởng kết quả trích xuất.
        }
      }, 700);
    }
    try {
      const formData = new FormData();
      files.forEach((file) => formData.append('files', file));
      if (useAi?.checked) formData.append('use_ai', '1');
      if (batchId) formData.append('batch_id', batchId);
      // Gọi API trích xuất thông tin từ (các) tệp ảnh/PDF đã tải lên; backend trả về các trường dữ liệu
      // đọc được (fields), độ tin cậy từng trường (field_confidence) và các xung đột giữa nhiều tệp (conflicts).
      const response = await fetch(apiUrl('/extract'), { method: 'POST', body: formData });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail ? `${result.error} ${result.detail}` : result.error);

      fillFields(result.fields, result.field_confidence || {}, true);
      markConflictingFields(result.conflicts);
      review.hidden = false;
      review.scrollIntoView({ behavior: 'smooth', block: 'start' });
      const processedCount = result.processed_file_count ?? files.length;
      showStatus(`Đã trích xuất và gộp thông tin từ ${processedCount}/${files.length} tệp. Hãy kiểm tra và chỉnh sửa trước khi tạo đơn.`, 'success');
    } catch (error) {
      showStatus(error.message || 'Không thể xử lý tệp này.', 'error');
    } finally {
      if (progressTimer) window.clearInterval(progressTimer);
      uploadInProgress = false;
      renderSelectedFiles();
      setBusy(button, false, 'Trích xuất thông tin');
    }
  });

  // Bấm nút "Bắt đầu (lại) phỏng vấn": khởi tạo phiên phỏng vấn mới trên backend, hiển thị câu hỏi
  // đầu tiên trong khung chat, và bật khu vực trả lời bằng giọng nói nếu trình duyệt hỗ trợ
  // (nếu không hỗ trợ thì mở sẵn phương án nhập bằng bàn phím).
  interviewStart.addEventListener('click', async () => {
    stopSpeaking();
    stopVoiceCapture();
    setBusy(interviewStart, true, 'Đang chuẩn bị…');
    try {
      // Gọi API bắt đầu phiên phỏng vấn mới cho loại mẫu đơn đang chọn; backend trả về session_id,
      // câu hỏi đầu tiên và tiến độ (progress) để hiển thị.
      const result = await jsonRequest(apiUrl('/interview/start'), { template_id: templateSelect.value });
      interviewSessionId = result.session_id;
      currentQuestion = result.question;
      pendingVoiceAnswer = '';
      voiceConfirmControls.hidden = true;
      interviewChat.replaceChildren();
      interviewChat.hidden = false;
      addChatMessage('assistant', result.message);
      addChatMessage('assistant', currentQuestion);
      showInterviewProgress(result.progress);
      keyboardFallback.hidden = false;
      if (voiceSupported) {
        initialiseRecognition();
        voiceControls.hidden = false;
        updateVietnameseVoiceStatus();
        setVoiceStatus('Câu hỏi được hiển thị bên trên. Bấm micro để trả lời bằng tiếng Việt.');
        speak(currentQuestion);
        showStatus('Trợ lý phỏng vấn giọng nói đã sẵn sàng.', 'success');
      } else {
        voiceControls.hidden = true;
        keyboardFallback.open = true;
        setVoiceStatus('Trình duyệt này chưa hỗ trợ nhận dạng giọng nói. Hãy dùng Chrome hoặc Edge, hoặc nhập bằng bàn phím.', 'error');
        showStatus('Trình duyệt này không hỗ trợ phỏng vấn giọng nói. Đã mở phương án nhập bằng bàn phím.', 'error');
      }
      if (!voiceSupported) interviewAnswer.focus();
    } catch (error) {
      showStatus(error.message || 'Không thể bắt đầu phỏng vấn.', 'error');
    } finally {
      setBusy(interviewStart, false, 'Bắt đầu lại phỏng vấn');
    }
  });

  // Bấm nút micro để bắt đầu/dừng ghi âm câu trả lời bằng giọng nói trong phần phỏng vấn.
  voiceAnswerButton.addEventListener('click', () => {
    if (!recognition || !interviewSessionId) return;
    if (listening) {
      recognition.stop();
      return;
    }
    stopSpeaking();
    recognition.lang = 'vi-VN';
    finalVoiceTranscript = [];
    voiceAnswerSubmitted = false;
    recognitionHadError = false;
    pendingVoiceAnswer = '';
    voiceConfirmControls.hidden = true;
    suppressRecognitionSubmission = false;
    pendingRecognitionSessionId = interviewSessionId;
    try {
      recognition.start();
    } catch (_error) {
      pendingRecognitionSessionId = null;
      setVoiceStatus('Micro đang khởi động. Vui lòng chờ một chút rồi thử lại.', 'error');
    }
  });

  // Bấm nút "Nghe lại câu hỏi": đọc lại câu hỏi hiện tại (chỉ khi không đang ghi âm).
  repeatQuestionButton.addEventListener('click', () => {
    if (listening) {
      setVoiceStatus('Hãy dừng micro trước khi nghe lại câu hỏi.', 'error');
      return;
    }
    speak(currentQuestion);
  });

  // Bấm "Xác nhận" bản chép lời giọng nói: gửi câu trả lời đã xác nhận lên backend.
  voiceConfirmButton.addEventListener('click', () => {
    if (!pendingVoiceAnswer) return;
    const answer = pendingVoiceAnswer;
    pendingVoiceAnswer = '';
    voiceConfirmControls.hidden = true;
    void submitInterviewAnswer(answer);
  });

  // Bấm "Nói lại": bỏ bản chép lời hiện tại và tự động bấm lại nút micro để ghi âm lần nữa.
  voiceRetryButton.addEventListener('click', () => {
    pendingVoiceAnswer = '';
    voiceConfirmControls.hidden = true;
    voiceTranscript.hidden = true;
    voiceAnswerButton.click();
  });

  // Khi người dùng rời trang (đóng tab/điều hướng đi nơi khác): dừng giọng đọc/ghi âm và báo backend
  // xóa phiên phỏng vấn đang dở (dùng keepalive để request vẫn có cơ hội hoàn tất dù trang đã unload).
  window.addEventListener('pagehide', () => {
    stopSpeaking();
    stopVoiceCapture();
    if (interviewSessionId) {
      fetch(apiUrl(`/interview/${encodeURIComponent(interviewSessionId)}`), { method: 'DELETE', keepalive: true }).catch(() => {});
    }
  });

  // Gửi câu trả lời khi người dùng nộp form phỏng vấn bằng bàn phím (nhấn Enter hoặc bấm nút "Gửi").
  interviewForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    await submitInterviewAnswer(interviewAnswer.value.trim());
  });

  // Xử lý khi người dùng bấm nút "Tải biểu mẫu" ở bước review: validate dữ liệu trước,
  // nếu hợp lệ mới gọi API tạo file .docx và tự động tải file đó về máy.
  dataForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = document.querySelector('#generate-button');
    const payload = { template_id: templateSelect.value, fields: fieldsPayload() };
    setBusy(button, true, 'Đang kiểm tra…');
    try {
      // Gọi API kiểm tra tính hợp lệ của dữ liệu đã nhập trước khi tạo file, tránh tạo ra file lỗi.
      const validationResponse = await fetch(apiUrl('/validate'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      const validation = await validationResponse.json();
      renderErrors(validation.errors);
      if (!validation.valid) {
        showStatus('Một số thông tin cần được hoàn thiện.', 'error');
        dataForm.querySelector('.invalid')?.focus();
        return;
      }
      setBusy(button, true, 'Đang tạo biểu mẫu…');
      // Gọi API tạo file .docx từ dữ liệu đã hợp lệ; phản hồi trả về là nội dung nhị phân của file.
      const response = await fetch(apiUrl('/generate'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const result = await response.json();
        throw new Error(result.error || 'Không thể tạo tệp biểu mẫu.');
      }
      // Chuyển blob nhận được thành URL tạm thời rồi kích hoạt tải xuống bằng một thẻ <a> ẩn,
      // sau đó giải phóng URL tạm để tránh rò rỉ bộ nhớ.
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = 'don-da-dien.docx';
      anchor.click();
      URL.revokeObjectURL(url);
      showStatus('Biểu mẫu đã được tạo và đang tải về máy của bạn.', 'success');
    } catch (error) {
      showStatus(error.message || 'Không thể tạo tệp biểu mẫu.', 'error');
    } finally {
      setBusy(button, false, 'Tải biểu mẫu');
    }
  });
})();
