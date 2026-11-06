"""Theo dõi tiến trình một lượt tải nhiều tệp lên /api/extract, trong RAM.

Không phải một hàng đợi tác vụ nền: batch vẫn được xử lý đồng bộ trong đúng
yêu cầu HTTP đó (xem ``_extract_batch`` trong app.py). Bộ theo dõi này chỉ
cho một yêu cầu polling riêng biết "đã xử lý xong bao nhiêu tệp" trong lúc
yêu cầu chính vẫn đang chạy, để giao diện hiện "Đang xử lý ảnh 2/5…" thay vì
một trạng thái "đang tải" chung im lìm suốt cả lượt. Dữ liệu chỉ sống trong
bộ nhớ của tiến trình gunicorn hiện tại (1 worker, 2 luồng) và luôn được xoá
khi yêu cầu chính kết thúc.
"""

from __future__ import annotations

import threading
import time

# Dọn batch bị bỏ lại nếu client rời trang giữa lúc tải lên (khi đó finish()
# không được gọi); không ảnh hưởng batch đang chạy bình thường vì luôn xong
# trong vài chục giây.
_STALE_AFTER_SECONDS = 5 * 60


class ExtractProgressTracker:
    """Sổ theo dõi tiến trình (done/total) của các batch trích xuất, an toàn giữa nhiều luồng."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._batches: dict[str, dict] = {}

    def start(self, batch_id: str, total: int) -> None:
        """Khởi tạo tiến trình cho một batch mới, đồng thời dọn các batch cũ bị bỏ lại."""
        with self._lock:
            self._prune_locked()
            self._batches[batch_id] = {"done": 0, "total": total, "started_at": time.monotonic()}

    def advance(self, batch_id: str) -> None:
        """Báo đã xử lý xong thêm một tệp trong batch."""
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is not None:
                batch["done"] += 1

    def get(self, batch_id: str) -> dict:
        """Lấy trạng thái hiện tại {done, total} của một batch; trả về 0/0 nếu không tồn tại."""
        with self._lock:
            batch = self._batches.get(batch_id)
            return {"done": batch["done"], "total": batch["total"]} if batch else {"done": 0, "total": 0}

    def finish(self, batch_id: str) -> None:
        """Xoá batch khỏi bộ theo dõi khi yêu cầu chính đã xử lý xong."""
        with self._lock:
            self._batches.pop(batch_id, None)

    def _prune_locked(self) -> None:
        """Xoá các batch đã quá hạn (client rời trang giữa chừng nên finish() không được gọi).

        Phải gọi trong lúc đã giữ ``self._lock``.
        """
        now = time.monotonic()
        stale = [key for key, batch in self._batches.items() if now - batch["started_at"] > _STALE_AFTER_SECONDS]
        for key in stale:
            self._batches.pop(key, None)
