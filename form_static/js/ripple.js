// Hiệu ứng "ripple" (gợn sóng) lan tỏa từ điểm bấm khi người dùng nhấn vào một nút bất kỳ trên trang.
(() => {
  // Tôn trọng cài đặt "giảm hiệu ứng chuyển động" của hệ điều hành/trình duyệt: nếu người dùng bật thì bỏ qua toàn bộ hiệu ứng.
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduceMotion) return;

  // Lắng nghe sự kiện click trên toàn tài liệu (event delegation) để áp dụng ripple cho mọi nút, kể cả nút được thêm động sau này.
  document.addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (!button || button.disabled) return;

    const rect = button.getBoundingClientRect();
    const size = Math.max(rect.width, rect.height);
    // event.detail === 0 nghĩa là click được kích hoạt không phải bằng chuột (vd: bàn phím/trình đọc màn hình) -> lấy tâm nút làm điểm phát ripple.
    const fromKeyboard = event.detail === 0;
    const x = (fromKeyboard ? rect.left + rect.width / 2 : event.clientX) - rect.left - size / 2;
    const y = (fromKeyboard ? rect.top + rect.height / 2 : event.clientY) - rect.top - size / 2;

    // Tạo phần tử <span class="ripple"> đặt đúng vị trí/kích thước vừa tính, CSS sẽ đảm nhiệm phần hoạt ảnh lan tỏa.
    const ripple = document.createElement('span');
    ripple.className = 'ripple';
    ripple.style.width = `${size}px`;
    ripple.style.height = `${size}px`;
    ripple.style.left = `${x}px`;
    ripple.style.top = `${y}px`;
    button.appendChild(ripple);
    // Tự xóa phần tử ripple ngay khi hoạt ảnh CSS kết thúc để không để lại rác trong DOM.
    ripple.addEventListener('animationend', () => ripple.remove(), { once: true });
  });
})();
