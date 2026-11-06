"""Các mô hình dữ liệu nội bộ.

Ứng dụng không ghi dữ liệu định danh vào cơ sở dữ liệu: hồ sơ chỉ sống trong
yêu cầu HTTP hiện tại và tệp xuất được xóa sau khi tải. Dataclass này là hợp
đồng dữ liệu giữa các tầng OCR, trích xuất và giao diện.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExtractionResult:
    """Kết quả trích xuất thông tin từ một hoặc nhiều tài liệu.

    - fields: giá trị từng trường đã nhận diện được (rỗng nếu chưa có).
    - confidence: độ tin cậy tương ứng của từng trường (vd. "missing", "low",
      "medium", "high").
    - warnings: các cảnh báo cần hiển thị cho người dùng (vd. OCR không rõ,
      dữ liệu cần đối chiếu lại).
    """

    fields: dict[str, str] = field(default_factory=dict)
    confidence: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
