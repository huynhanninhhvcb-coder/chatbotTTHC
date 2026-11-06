"""Điểm vào của ứng dụng điền biểu mẫu từ ảnh/PDF."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
import io
from pathlib import Path
from threading import Lock
from uuid import uuid4

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

from config import Config
from services.ai_extraction_service import AIExtractionError, extract_missing_fields, is_configured
from services.batch_extraction_service import merge_extraction_results
from services.docx_service import DocumentGenerationError, generate_document
from services.extract_progress_service import ExtractProgressTracker
from services.extraction_service import FIELD_NAMES, extract_personal_information
from services.form_options import FIELD_OPTIONS
from services.interview_service import TEMPLATE_STEPS, InterviewNotFoundError, InterviewService
from services.ocr_service import OCRResult, OCRUnavailableError, ocr_image
from services.pdf_service import PDFProcessingError, process_pdf
from services.template_service import TEMPLATE_GROUPS, TEMPLATES, get_template, list_templates
from services.validation_service import validate_form_data
from models.database import ExtractionResult


DEFAULT_TEMPLATE_ID = "tro_cap_huu_tri"


class UploadTooLargeError(ValueError):
    """A single upload exceeded the per-file batch limit."""


def _file_extension(filename: str) -> str:
    """Đuôi mở rộng (chữ thường, không dấu chấm) của tên tệp; rỗng nếu không có."""
    return filename.rsplit(".", 1)[1].lower() if "." in filename else ""


def _allowed_file(filename: str, allowed_extensions: set[str]) -> bool:
    """Kiểm tra tên tệp có đuôi mở rộng nằm trong danh sách cho phép hay không."""
    return _file_extension(filename) in allowed_extensions


def _display_filename(filename: str) -> str:
    """Return a display-only basename without ever using it as a filesystem path."""
    return filename.replace("\\", "/").rsplit("/", 1)[-1] or "tệp không tên"


def _temporary_upload_path(app: Flask, extension: str) -> Path:
    """Đường dẫn tạm (tên ngẫu nhiên) để lưu tệp tải lên trong lúc xử lý."""
    return Path(app.config["UPLOAD_FOLDER"]) / f"{uuid4().hex}.{extension}"


def _megabytes(size: int) -> int:
    return size // (1024 * 1024)


def _requested_template(payload: dict):
    """Mẫu đơn mà yêu cầu chọn (mặc định: trợ cấp hưu trí xã hội), hoặc None nếu không có."""
    return get_template(payload.get("template_id", DEFAULT_TEMPLATE_ID))


def _selected_uploads() -> list:
    """Read the new `files` field and retain compatibility with legacy `file`."""
    uploads = [upload for upload in request.files.getlist("files") if upload and upload.filename]
    if not uploads:
        uploads = [upload for upload in request.files.getlist("file") if upload and upload.filename]
    return uploads


def _save_upload_with_limit(upload, destination: Path, maximum_size: int) -> int:
    """Stream an upload to a temporary file while enforcing its individual limit."""
    total_size = 0
    with destination.open("wb") as output:
        while chunk := upload.stream.read(1024 * 1024):
            total_size += len(chunk)
            if total_size > maximum_size:
                raise UploadTooLargeError
            output.write(chunk)
    return total_size


def _unique_warnings(warnings: list[str]) -> list[str]:
    """Loại bỏ cảnh báo trùng lặp và rỗng, giữ nguyên thứ tự xuất hiện đầu tiên."""
    return list(dict.fromkeys(warning for warning in warnings if warning))


def _ai_requested() -> bool:
    """Require explicit consent before sending a citizen document externally."""
    return request.form.get("use_ai", "").strip().lower() in {"1", "true", "yes", "on"}


# Chỉ những trường OCR/regex thực sự cố nhận diện từ giấy tờ mới đáng gọi AI bổ
# sung; các trường như thông tin người giám hộ được thu thập qua phỏng vấn,
# không nằm trên giấy tờ nên hỏi AI cũng sẽ luôn trống.
_AI_EXTRACTABLE_FIELDS = (
    "full_name",
    "date_of_birth",
    "gender",
    "ethnic_group",
    "citizen_id",
    "citizen_id_issue_date",
    "citizen_id_issue_place",
    "residence_address",
    "contact_address",
    "temporary_address",
    "phone_number",
    "occupation",
    "employer",
    "support_category",
    "support_detail",
    "request_content",
    "deceased_death_hour",
    "deceased_death_minute",
    "payment_method",
    "cremation_support_category",
    "cremation_support_detail",
    "bank_account_name",
    "bank_account_number",
    "bank_name",
    "deceased_full_name",
    "deceased_date_of_birth",
    "deceased_gender",
    "deceased_ethnic_group",
    "deceased_nationality",
    "deceased_citizen_id",
    "deceased_residence_address",
    "deceased_death_date",
    "deceased_death_place",
    "deceased_death_cause",
    "death_certificate_number",
    "death_certificate_date",
    "death_certificate_issuer",
)


# Nếu AI trực tiếp gặp lỗi trong một batch, các tệp có thể cùng rơi về Tesseract.
# Khóa này giữ đúng một OCR tại một thời điểm trên Render Free; các lời gọi AI
# bình thường vẫn được chạy song song vì chúng không tiêu tốn CPU của Render.
_AI_FALLBACK_OCR_LOCK = Lock()


def _ask_ai(app: Flask, filepath: Path, extension: str, fields) -> dict[str, str]:
    """Nhờ GPT vision đọc các trường ``fields`` trên tài liệu gốc."""
    return extract_missing_fields(
        filepath,
        extension,
        list(fields),
        api_key=app.config["OPENAI_API_KEY"],
        model=app.config["OPENAI_MODEL"],
        max_pages=app.config["MAX_PDF_PAGES"],
    )


def _direct_ai_extraction(app: Flask, filepath: Path, extension: str) -> tuple[OCRResult, ExtractionResult] | None:
    """Đọc tài liệu trực tiếp bằng vision khi người dân đã đồng ý gửi tới OpenAI."""
    ai_fields = _ask_ai(app, filepath, extension, _AI_EXTRACTABLE_FIELDS)
    if not ai_fields:
        return None

    # Khởi tạo đầy đủ mọi trường của biểu mẫu (rỗng/"missing") trước, vì AI chỉ
    # trả về những trường nó tìm thấy; các trường còn lại vẫn cần xuất hiện để
    # giao diện hiển thị và người dùng tự điền tay.
    fields = {field: "" for field in FIELD_NAMES}
    confidence = {field: "missing" for field in FIELD_NAMES}
    for field, value in ai_fields.items():
        if field not in fields or not value:
            continue
        fields[field] = value
        confidence[field] = "medium"

    processed = OCRResult(
        text="",
        warnings=[
            "Tệp được AI nhận diện trực tiếp để tránh thời gian chờ OCR trên máy chủ; "
            "hãy đối chiếu giấy tờ gốc trước khi tạo đơn."
        ],
        source="ai_vision",
        # Không lưu bản chép nội dung tài liệu; phần tử rỗng chỉ biểu thị một
        # tài liệu đã được xử lý trong metadata phản hồi.
        pages=[""],
    )
    return processed, ExtractionResult(fields=fields, confidence=confidence)


def _local_ocr_extraction(app: Flask, filepath: Path, extension: str) -> tuple[OCRResult, ExtractionResult]:
    """Chạy OCR cục bộ (Tesseract) cho PDF hoặc ảnh, sau đó trích xuất thông tin cá nhân từ văn bản đọc được."""
    if extension == "pdf":
        processed = process_pdf(
            filepath,
            max_pages=app.config["MAX_PDF_PAGES"],
            tesseract_cmd=app.config.get("TESSERACT_CMD"),
            max_workers=app.config["OCR_MAX_WORKERS"],
            target_width=app.config["OCR_TARGET_WIDTH"],
            preferred_language=app.config.get("OCR_LANGUAGE"),
            fast_mode=app.config["OCR_FAST_MODE"],
        )
    else:
        processed = ocr_image(
            filepath,
            tesseract_cmd=app.config.get("TESSERACT_CMD"),
            max_workers=app.config["OCR_MAX_WORKERS"],
            target_width=app.config["OCR_TARGET_WIDTH"],
            preferred_language=app.config.get("OCR_LANGUAGE"),
            fast_mode=app.config["OCR_FAST_MODE"],
        )
    extracted = extract_personal_information(
        processed.text,
        page_texts=processed.pages,
        cccd_mrz_texts=processed.mrz_texts,
    )
    return processed, extracted


def _process_upload_content(
    app: Flask,
    filepath: Path,
    extension: str,
    *,
    ai_consented: bool,
) -> tuple[OCRResult, ExtractionResult]:
    """Ưu tiên AI vision khi có đồng ý; nếu API lỗi thì quay về OCR cục bộ."""
    use_direct_ai = ai_consented and is_configured(app.config)
    ai_warning = ""
    if use_direct_ai:
        try:
            direct_result = _direct_ai_extraction(app, filepath, extension)
        except AIExtractionError as error:
            ai_warning = f"AI nhận diện trực tiếp không khả dụng ({error}); đã chuyển sang OCR cục bộ."
        else:
            if direct_result is not None:
                return direct_result
            ai_warning = "AI không nhận diện được trường thông tin nào; đã chuyển sang OCR cục bộ."

    # Một batch AI có thể gọi nhiều tệp đồng thời. Chỉ khóa nhánh fallback để
    # không vô tình chạy nhiều Tesseract cùng lúc khi OpenAI gặp sự cố.
    fallback_context = _AI_FALLBACK_OCR_LOCK if use_direct_ai else nullcontext()
    with fallback_context:
        processed, extracted = _local_ocr_extraction(app, filepath, extension)
    if ai_warning:
        processed.warnings.append(ai_warning)
    else:
        _enhance_with_ai(app, filepath, extension, processed, extracted, consented=ai_consented)
    return processed, extracted


def _enhance_with_ai(
    app: Flask,
    filepath: Path,
    extension: str,
    processed,
    extracted,
    *,
    consented: bool,
) -> None:
    """Gọi GPT vision để bổ sung trường còn thiếu, nếu đã cấu hình OPENAI_API_KEY.

    Phải chạy trước khi tệp tạm bị xóa vì cần gửi ảnh gốc (không phải văn bản
    OCR) cho AI đọc lại.
    """
    if not consented or not is_configured(app.config):
        return

    # Đoán loại giấy tờ dựa trên từ khóa xuất hiện trong văn bản OCR, để chỉ nhờ
    # AI bổ sung những trường phù hợp với loại tài liệu đó thay vì hỏi tất cả
    # _AI_EXTRACTABLE_FIELDS (tốn lượt gọi API và dễ khiến AI đoán bừa các
    # trường không có trên giấy tờ).
    folded_text = processed.text.casefold()
    if any(marker in folded_text for marker in ("tài khoản", "tai khoan", "ngân hàng", "ngan hang")):
        relevant_fields = ("bank_account_name", "bank_account_number", "bank_name")
    elif any(marker in folded_text for marker in ("căn cước", "can cuoc", "citizen identity", "idvnm", "1dvnm")):
        relevant_fields = (
            "full_name",
            "date_of_birth",
            "gender",
            "citizen_id",
            "residence_address",
            "contact_address",
        )
    else:
        relevant_fields = _AI_EXTRACTABLE_FIELDS

    # Chỉ hỏi AI những trường OCR/regex chưa đọc được hoặc đọc với độ tin cậy
    # thấp/trung bình; trường đã có độ tin cậy cao thì giữ nguyên, không ghi đè.
    review_fields = [
        field
        for field in relevant_fields
        if not extracted.fields.get(field)
        or extracted.confidence.get(field, "missing") in {"missing", "low", "medium"}
    ]
    if not review_fields:
        return
    try:
        ai_fields = _ask_ai(app, filepath, extension, review_fields)
    except AIExtractionError as error:
        processed.warnings.append(f"Không thể dùng AI để bổ sung dữ liệu còn thiếu: {error}")
        return
    if not ai_fields:
        return
    for field, value in ai_fields.items():
        extracted.fields[field] = value
        extracted.confidence[field] = "medium"
    processed.warnings.append(
        "Một số trường được ChatGPT hỗ trợ nhận diện do OCR không đọc được; hãy kiểm tra kỹ trước khi tạo đơn."
    )


def _file_summary(name: str, processed: OCRResult, extracted: ExtractionResult) -> dict:
    """Thông tin một tệp đã xử lý xong, hiển thị trong danh sách tệp của giao diện."""
    return {
        "name": name,
        "status": "processed",
        "source": processed.source,
        "page_count": len(processed.pages),
        "extracted_fields": [field for field, value in extracted.fields.items() if value],
    }


def _combined_raw_text(records: list[dict], maximum_length: int = 12000) -> str:
    """Keep an inspectable, bounded OCR preview for a multi-document request."""
    chunks: list[str] = []
    remaining = maximum_length
    for record in records:
        header = f"--- Tệp: {record['name']} ---\n"
        text = record["processed"].text.strip()
        chunk = f"{header}{text}".strip()
        if not chunk or remaining <= 0:
            break
        if len(chunk) > remaining:
            chunks.append(chunk[:remaining])
            break
        chunks.append(chunk)
        remaining -= len(chunk) + 2
    return "\n\n".join(chunks)


def _process_saved_upload(app: Flask, name: str, filepath: Path, extension: str, ai_consented: bool) -> dict:
    """Chạy OCR + trích xuất cho một tệp đã lưu tạm; luôn dọn tệp khi xong.

    Tách khỏi vòng lặp lưu tệp để nhiều tệp trong cùng một lượt tải lên có
    thể được OCR song song (xem ``_extract_batch``): đây là phần chậm nhất
    của yêu cầu và độc lập giữa các tệp.
    """
    try:
        processed, extracted = _process_upload_content(
            app,
            filepath,
            extension,
            ai_consented=ai_consented,
        )
    except OCRUnavailableError as error:
        return {
            "name": name,
            "status": "error",
            "message": "Không thể OCR tệp quét trên máy chủ này.",
            "detail": str(error),
            "code": "ocr_unavailable",
        }
    except (PDFProcessingError, OSError, ValueError) as error:
        return {
            "name": name,
            "status": "error",
            "message": "Không thể đọc tệp đã tải lên.",
            "detail": str(error),
            "code": "unreadable_file",
        }
    finally:
        # Tệp nguồn chứa dữ liệu cá nhân chỉ tồn tại trong suốt yêu cầu này.
        filepath.unlink(missing_ok=True)
    return {"name": name, "status": "processed", "processed": processed, "extracted": extracted}


def _extract_batch(app: Flask, uploads: list):
    """Process each document independently, then merge only reviewable fields.

    A corrupt scan must not discard useful information read from the other
    documents.  Limits, however, fail the whole request because the user needs
    to reduce the selected batch before retrying.

    Saving (fast, must stay sequential to enforce the running batch-size
    total) and OCR (slow, each file spawns several independent Tesseract
    subprocesses) are split into two passes. With AI consent the second pass
    runs files concurrently (the work happens at OpenAI); local OCR runs one
    file at a time because each file already OCRs its pages in parallel, and
    running files concurrently too would multiply Tesseract processes on a
    small server.
    """
    processed_files: list[dict] = []
    failures: list[dict] = []
    uploaded_size = 0
    ai_consented = _ai_requested()
    maximum_file_size = app.config["MAX_FILE_SIZE"]
    maximum_batch_size = app.config["MAX_BATCH_TOTAL_SIZE"]
    file_size_mb = _megabytes(maximum_file_size)
    batch_size_mb = _megabytes(maximum_batch_size)

    # Giữ vị trí tải lên ban đầu cho processed_files: tệp lỗi ngay khi lưu (hiếm,
    # ví dụ hết dung lượng đĩa) không được chạy qua OCR song song bên dưới, nên
    # phải ghi nhớ chỉ số gốc để không xáo trộn thứ tự hiển thị so với các tệp
    # còn lại của cùng lượt tải lên.
    processed_files_by_index: dict[int, dict] = {}
    saved_files: list[tuple[int, str, Path, str]] = []

    def reject_batch(message: str):
        # Từ chối cả lượt tải lên: xóa ngay các tệp của lượt này đã lưu xuống
        # đĩa (ảnh giấy tờ cá nhân), không để sót lại trong uploads/.
        for _index, _name, saved_path, _extension in saved_files:
            saved_path.unlink(missing_ok=True)
        return jsonify(error=message), 413

    for index, upload in enumerate(uploads):
        name = _display_filename(upload.filename)
        extension = _file_extension(upload.filename)
        # Kiểm tra sớm dựa trên content-length trình duyệt khai báo (nếu có) để
        # chặn tệp/batch quá lớn trước khi phải đọc hết dữ liệu; _save_upload_with_limit
        # bên dưới vẫn kiểm tra lại theo dung lượng thực đọc được.
        declared_size = upload.content_length or 0
        if declared_size > maximum_file_size:
            return reject_batch(f"Tệp \"{name}\" lớn hơn giới hạn {file_size_mb} MB mỗi tệp.")
        if declared_size and uploaded_size + declared_size > maximum_batch_size:
            return reject_batch(f"Tổng dung lượng các tệp vượt quá {batch_size_mb} MB.")

        remaining_size = maximum_batch_size - uploaded_size
        if remaining_size <= 0:
            return reject_batch(f"Tổng dung lượng các tệp vượt quá {batch_size_mb} MB.")

        filepath = _temporary_upload_path(app, extension)
        save_limit = min(maximum_file_size, remaining_size)
        try:
            uploaded_size += _save_upload_with_limit(upload, filepath, save_limit)
        except UploadTooLargeError:
            filepath.unlink(missing_ok=True)
            # save_limit nhỏ hơn giới hạn từng tệp nghĩa là chính batch đã đầy,
            # không phải riêng tệp này quá lớn; thông báo lỗi cần phân biệt rõ.
            error = (
                f"Tổng dung lượng các tệp vượt quá {batch_size_mb} MB."
                if save_limit < maximum_file_size
                else f"Tệp \"{name}\" lớn hơn giới hạn {file_size_mb} MB mỗi tệp."
            )
            return reject_batch(error)
        except OSError as error:
            filepath.unlink(missing_ok=True)
            # Lỗi ghi đĩa cục bộ (hiếm): chỉ tệp này thất bại, các tệp khác trong
            # batch vẫn tiếp tục được xử lý bình thường.
            failure = {
                "name": name,
                "message": "Không thể đọc tệp đã tải lên.",
                "detail": str(error),
                "code": "unreadable_file",
            }
            failures.append(failure)
            processed_files_by_index[index] = {"name": name, "status": "error", **failure}
            continue
        saved_files.append((index, name, filepath, extension))

    progress_tracker = app.extensions["extract_progress"]
    batch_id = request.form.get("batch_id", "").strip() or None
    if saved_files:
        if batch_id:
            progress_tracker.start(batch_id, len(saved_files))
        try:
            # AI chạy ở hạ tầng OpenAI nên có thể xử lý nhiều tệp đồng thời mà
            # không tranh 0.1 CPU của Render. OCR cục bộ xử lý lần lượt từng tệp:
            # bên trong mỗi tệp đã chạy song song theo trang (OCR_MAX_WORKERS), nên
            # chạy nhiều tệp cùng lúc sẽ nhân số tiến trình Tesseract lên (5 x 5).
            worker_limit = (
                app.config["AI_MAX_WORKERS"]
                if ai_consented and is_configured(app.config)
                else 1
            )
            batch_workers = min(len(saved_files), worker_limit)
            with ThreadPoolExecutor(max_workers=batch_workers) as executor:
                futures = {
                    executor.submit(_process_saved_upload, app, item[1], item[2], item[3], ai_consented): item
                    for item in saved_files
                }
                # Báo tiến trình theo đúng thứ tự tệp thực sự xong (đường AI có
                # thể xử lý vài tệp đồng thời nên không nhất thiết theo thứ tự
                # tải lên); vẫn dựng lại outcomes theo thứ tự gốc bên dưới vì
                # phần ghép kết quả dựa vào đúng thứ tự đó.
                outcomes_by_item = {}
                for future in as_completed(futures):
                    outcomes_by_item[futures[future]] = future.result()
                    if batch_id:
                        progress_tracker.advance(batch_id)
                outcomes = [outcomes_by_item[item] for item in saved_files]
        finally:
            if batch_id:
                progress_tracker.finish(batch_id)
    else:
        outcomes = []

    records: list[dict] = []
    for (index, _name, _filepath, _extension), outcome in zip(saved_files, outcomes):
        if outcome["status"] == "processed":
            records.append(outcome)
            processed_files_by_index[index] = _file_summary(
                outcome["name"], outcome["processed"], outcome["extracted"]
            )
        else:
            failure = {key: outcome[key] for key in ("name", "message", "detail", "code")}
            failures.append(failure)
            processed_files_by_index[index] = {"name": outcome["name"], "status": "error", **failure}

    processed_files = [processed_files_by_index[index] for index in sorted(processed_files_by_index)]

    if not records:
        response_code = 503 if failures and all(item["code"] == "ocr_unavailable" for item in failures) else 422
        error = (
            "Không thể OCR các tệp quét trên máy chủ này."
            if response_code == 503
            else "Không thể đọc các tệp đã tải lên."
        )
        return jsonify(error=error, processed_files=processed_files), response_code

    # Ghép kết quả trích xuất của mọi tệp thành một bộ trường duy nhất; nếu các
    # tệp có giá trị khác nhau cho cùng một trường, phần khác biệt được báo qua
    # merged.conflicts để người dùng tự chọn.
    merged = merge_extraction_results(record["extracted"] for record in records)
    validation = validate_form_data(merged.result.fields, required_fields=())
    warnings = list(merged.result.warnings)
    for record in records:
        warnings.extend(f"{record['name']}: {warning}" for warning in record["processed"].warnings)
    warnings.extend(f"{failure['name']}: {failure['message']}" for failure in failures)
    warnings.extend(validation.warnings)

    return jsonify(
        fields=merged.result.fields,
        field_confidence=merged.result.confidence,
        warnings=_unique_warnings(warnings),
        source="batch",
        processed_file_count=len(records),
        processed_files=processed_files,
        conflicts=[{"field": conflict.field, "label": conflict.label} for conflict in merged.conflicts],
        # Giữ văn bản đối chiếu trong phản hồi hiện tại; không ghi ra cơ sở dữ liệu.
        raw_text=_combined_raw_text(records),
    )


def create_app(test_config: dict | None = None) -> Flask:
    """Application factory: khởi tạo Flask app, nạp cấu hình, tạo thư mục cần thiết và đăng ký toàn bộ route."""
    app = Flask(__name__, template_folder="form_templates", static_folder="form_static")
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    for folder_name in ("UPLOAD_FOLDER", "OUTPUT_FOLDER", "TEMPLATE_FOLDER"):
        Path(app.config[folder_name]).mkdir(parents=True, exist_ok=True)
    app.extensions["interview_service"] = InterviewService(app.config["INTERVIEW_TTL_SECONDS"])
    app.extensions["extract_progress"] = ExtractProgressTracker()

    @app.get("/")
    def index():
        """Trang chủ: giao diện tải tệp/điền biểu mẫu."""
        return render_template(
            "index.html",
            templates=list_templates(),
            ai_configured=is_configured(app.config),
        )

    @app.get("/health")
    def health():
        """Endpoint kiểm tra tình trạng máy chủ và cấu hình OCR/AI hiện tại (dùng để giám sát/health check)."""
        return jsonify(
            status="ok",
            tesseract_configured=bool(app.config.get("TESSERACT_CMD")),
            ai_configured=is_configured(app.config),
            ai_model=app.config.get("OPENAI_MODEL") if is_configured(app.config) else None,
            ai_max_workers=app.config["AI_MAX_WORKERS"],
            ocr={
                "max_workers": app.config["OCR_MAX_WORKERS"],
                "target_width": app.config["OCR_TARGET_WIDTH"],
                "language": app.config.get("OCR_LANGUAGE") or "automatic",
                "fast_mode": app.config["OCR_FAST_MODE"],
            },
        )

    @app.get("/api/extract/progress/<batch_id>")
    def extract_progress(batch_id: str):
        """Trả về tiến độ xử lý (số tệp đã xong/tổng số) của một batch OCR đang chạy."""
        # Không phải trạng thái xác thực gì: chỉ để giao diện hỏi "đã xử lý
        # xong bao nhiêu tệp" trong khi yêu cầu /api/extract chính vẫn đang
        # chạy. Trả {done: 0, total: 0} nếu batch_id không tồn tại (đã xong,
        # bị dọn vì quá cũ, hoặc chưa từng có) để client không cần phân biệt.
        return jsonify(app.extensions["extract_progress"].get(batch_id))

    @app.post("/api/extract")
    def extract():
        """Nhận một hoặc nhiều tệp PDF/ảnh, chạy OCR/AI để trích xuất thông tin cá
        nhân và trả về các trường đã nhận diện kèm độ tin cậy và cảnh báo."""
        uploads = _selected_uploads()
        if not uploads:
            return jsonify(error="Vui lòng chọn ít nhất một tệp PDF hoặc ảnh."), 400
        if len(uploads) > app.config["MAX_UPLOAD_FILES"]:
            return jsonify(
                error=f"Chỉ có thể tải tối đa {app.config['MAX_UPLOAD_FILES']} tệp trong một lần quét."
            ), 400

        unsupported = [
            _display_filename(upload.filename)
            for upload in uploads
            if not _allowed_file(upload.filename, app.config["ALLOWED_EXTENSIONS"])
        ]
        if unsupported:
            return jsonify(
                error="Định dạng chưa được hỗ trợ. Chỉ nhận PDF, PNG, JPG hoặc JPEG.",
                files=unsupported,
            ), 400

        # Nhiều tệp: chuyển sang luồng xử lý riêng có OCR song song (xem _extract_batch).
        if len(uploads) > 1:
            return _extract_batch(app, uploads)

        upload = uploads[0]
        too_large_error = f"Mỗi tệp chỉ được tối đa {_megabytes(app.config['MAX_FILE_SIZE'])} MB."
        if upload.content_length and upload.content_length > app.config["MAX_FILE_SIZE"]:
            return jsonify(error=too_large_error), 413

        extension = _file_extension(upload.filename)
        filepath = _temporary_upload_path(app, extension)

        try:
            _save_upload_with_limit(upload, filepath, app.config["MAX_FILE_SIZE"])
            processed, extracted = _process_upload_content(
                app,
                filepath,
                extension,
                ai_consented=_ai_requested(),
            )
        except OCRUnavailableError as error:
            return jsonify(
                error="Không thể OCR tệp quét trên máy chủ này.",
                detail=str(error),
                code="ocr_unavailable",
            ), 503
        except UploadTooLargeError:
            return jsonify(error=too_large_error), 413
        except (PDFProcessingError, OSError, ValueError) as error:
            return jsonify(error="Không thể đọc tệp đã tải lên.", detail=str(error)), 422
        finally:
            # Tệp nguồn chứa dữ liệu cá nhân chỉ tồn tại trong lúc xử lý yêu cầu.
            filepath.unlink(missing_ok=True)

        validation = validate_form_data(extracted.fields, required_fields=())
        return jsonify(
            fields=extracted.fields,
            field_confidence=extracted.confidence,
            warnings=[*processed.warnings, *extracted.warnings, *validation.warnings],
            source=processed.source,
            processed_file_count=1,
            processed_files=[_file_summary(_display_filename(upload.filename), processed, extracted)],
            # Giúp người dân đối chiếu; giao diện không lưu nội dung này sau khi tải lại.
            raw_text=processed.text[:12000],
        )

    @app.get("/api/templates")
    def template_catalog():
        """Danh mục mẫu đơn cho chatbot: nhóm, tên ngắn, từ khoá nhận diện, trường bắt buộc và nhãn hiển thị."""
        templates = []
        for template in TEMPLATES.values():
            field_order = list(dict.fromkeys(step.field for step in TEMPLATE_STEPS.get(template.id, ())))
            field_order += [name for name in template.required_fields if name not in field_order]
            templates.append(
                {
                    "id": template.id,
                    "title": template.title,
                    "short_title": template.short_title,
                    "group": template.group,
                    "family": template.family_id,
                    "variant": template.variant,
                    "keywords": list(template.keywords),
                    "required_fields": list(template.required_fields),
                    "fields": [
                        {"name": name, "label": template.labels.get(name) or FIELD_NAMES.get(name, name)}
                        for name in field_order
                    ],
                }
            )
        groups = [{"id": key, "title": title} for key, title in TEMPLATE_GROUPS.items()]
        return jsonify(groups=groups, templates=templates, field_labels=FIELD_NAMES, options=FIELD_OPTIONS)

    @app.post("/api/validate")
    def validate():
        """Kiểm tra dữ liệu các trường của một biểu mẫu theo quy tắc của template tương ứng."""
        payload = request.get_json(silent=True) or {}
        template = _requested_template(payload)
        if template is None:
            return jsonify(error="Biểu mẫu không tồn tại."), 404
        validation = validate_form_data(payload.get("fields", {}), required_fields=template.required_fields)
        return jsonify(valid=validation.valid, errors=validation.errors, warnings=validation.warnings)

    @app.post("/api/interview/start")
    def start_interview():
        """Bắt đầu một phiên phỏng vấn hỏi-đáp để thu thập dữ liệu còn thiếu cho biểu mẫu được chọn."""
        payload = request.get_json(silent=True) or {}
        template = _requested_template(payload)
        if template is None:
            return jsonify(error="Biểu mẫu không tồn tại."), 404
        prefilled = payload.get("prefilled_fields") or None
        return jsonify(app.extensions["interview_service"].start(template, prefilled))

    @app.post("/api/interview/answer")
    def answer_interview():
        """Ghi nhận câu trả lời của người dùng cho phiên phỏng vấn và trả về câu hỏi/kết quả tiếp theo."""
        payload = request.get_json(silent=True) or {}
        session_id = payload.get("session_id", "")
        try:
            response = app.extensions["interview_service"].answer(session_id, str(payload.get("answer", "")))
        except InterviewNotFoundError as error:
            return jsonify(error=str(error), code="interview_expired"), 410
        return jsonify(response)

    @app.delete("/api/interview/<session_id>")
    def discard_interview(session_id: str):
        """Hủy một phiên phỏng vấn đang dang dở (ví dụ khi người dùng rời trang)."""
        app.extensions["interview_service"].discard(session_id)
        return "", 204

    @app.post("/api/generate")
    def generate():
        """Sinh tệp Word (.docx) từ dữ liệu các trường đã điền theo template được
        chọn, và trả về cho người dùng tải xuống."""
        payload = request.get_json(silent=True) or {}
        template = _requested_template(payload)
        if template is None:
            return jsonify(error="Biểu mẫu không tồn tại."), 404

        fields = payload.get("fields", {})
        # "Tạo đơn dù còn thiếu": người dân chủ động tải đơn khi chưa đủ hoặc
        # chưa đúng thông tin, nên bỏ qua kiểm tra; ô còn thiếu để trống cho
        # họ tự điền tay.
        if not payload.get("allow_incomplete"):
            validation = validate_form_data(fields, required_fields=template.required_fields)
            if not validation.valid:
                return jsonify(error="Vui lòng hoàn thiện các trường bắt buộc.", errors=validation.errors), 422

        output_path = Path(app.config["OUTPUT_FOLDER"]) / f"{template.id}_{uuid4().hex}.docx"
        try:
            generate_document(template, fields, output_path)
        except DocumentGenerationError as error:
            return jsonify(error="Không thể tạo tệp Word.", detail=str(error)), 500

        # Đọc vào bộ nhớ rồi xóa ngay: tệp chứa dữ liệu cá nhân không phụ thuộc
        # vào việc trình duyệt đóng kết nối tải xuống để được dọn dẹp.
        try:
            download_buffer = io.BytesIO(output_path.read_bytes())
        finally:
            output_path.unlink(missing_ok=True)

        return send_file(
            download_buffer,
            as_attachment=True,
            download_name=f"{template.download_name}.docx",
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    @app.errorhandler(RequestEntityTooLarge)
    def file_too_large(_error):
        """Bắt lỗi khi Flask từ chối request vượt quá MAX_CONTENT_LENGTH, trả về thông báo thân thiện thay vì lỗi 413 mặc định."""
        maximum_batch_size = _megabytes(app.config["MAX_BATCH_TOTAL_SIZE"])
        return jsonify(error=f"Tổng dung lượng các tệp vượt quá {maximum_batch_size} MB."), 413

    return app


# Instance mặc định dùng khi chạy trực tiếp (`python app.py`) hoặc khi một WSGI
# server (vd. gunicorn) import module này.
app = create_app()


if __name__ == "__main__":
    # threaded=True để một yêu cầu OCR/trích xuất kéo dài vài giây không chặn
    # các yêu cầu khác (vd. /health, tài nguyên tĩnh, hoặc một tab trình duyệt
    # khác) trên máy chủ phát triển đơn luồng mặc định của Flask.
    app.run(debug=True, threaded=True)
