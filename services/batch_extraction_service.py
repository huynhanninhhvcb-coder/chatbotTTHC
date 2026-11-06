"""Gộp thông tin trích xuất từ nhiều tệp giấy tờ.

Mỗi tệp được trích xuất độc lập trước khi đi vào mô-đun này.  Cách làm đó
giúp một ảnh CCCD rõ có thể bổ sung cho một PDF biểu mẫu, nhưng không để một
giá trị OCR kém chất lượng ghi đè dữ liệu đáng tin cậy hơn.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final
import unicodedata

from models.database import ExtractionResult
from services.extraction_service import FIELD_NAMES
from utils.text_utils import (
    normalize_citizen_id,
    normalize_date,
    normalize_phone,
    normalize_text,
    strip_accents,
)


# Điểm ưu tiên để so sánh độ tin cậy giữa các giá trị ứng viên khi gộp nhiều
# tệp: confidence cao hơn luôn thắng, bất kể tệp nào xuất hiện trước.
_CONFIDENCE_RANK: Final = {
    "high": 3,
    "medium": 2,
    "low": 1,
    "missing": 0,
}
# Các trường này được so sánh theo giá trị đã chuẩn hóa (số/ngày) thay vì so
# chuỗi thô, để các cách viết khác nhau của cùng một giá trị không bị tính
# nhầm thành xung đột.
_IDENTIFIER_FIELDS: Final = {"citizen_id", "guardian_citizen_id"}
_PHONE_FIELDS: Final = {"phone_number", "guardian_phone"}
_DATE_FIELDS: Final = {"date_of_birth", "guardian_date_of_birth"}
# Trường họ tên được nhóm theo biến thể có dấu/không dấu (xem _same_name_variant)
# vì MRZ trên CCCD luôn không dấu, không nên bị coi là mâu thuẫn với mặt trước.
_NAME_FIELDS: Final = {"full_name", "guardian_full_name"}
# Các trường dùng để xác định tệp nào là "hồ sơ chính" (của người đứng đơn)
# khi nhiều tệp trong cùng một lượt trích xuất có thể thuộc về nhiều người khác nhau.
_PROFILE_IDENTITY_FIELDS: Final = ("citizen_id", "full_name", "date_of_birth")
_GENERIC_OCR_WARNING: Final = (
    "Dữ liệu được suy luận từ OCR/PDF. Hãy kiểm tra kỹ mọi trường trước khi tải đơn."
)
_MISSING_CITIZEN_ID_WARNING: Final = "Không nhận diện được số CCCD/định danh 12 chữ số."
_MISSING_FULL_NAME_WARNING: Final = "Không nhận diện được họ và tên đáng tin cậy từ hồ sơ."


@dataclass(frozen=True)
class FieldConflict:
    """Các giá trị không trống, khác nhau của một trường giữa nhiều tệp."""

    field: str
    label: str
    selected_value: str
    alternative_values: tuple[str, ...]
    source_indexes: tuple[int, ...]


@dataclass
class BatchExtractionResult:
    """Kết quả gộp và các trường cần người dân kiểm tra lại."""

    result: ExtractionResult
    conflicts: list[FieldConflict]

    @property
    def warnings(self) -> list[str]:
        return self.result.warnings


@dataclass(frozen=True)
class _FieldValue:
    """Một giá trị ứng viên cho một trường, kèm thông tin để xếp hạng/so sánh
    khi chọn ra giá trị đại diện giữa nhiều tệp."""

    value: str
    comparison_key: str
    confidence: str
    rank: int
    source_index: int


def _normalise_confidence(value: object, *, has_value: bool) -> tuple[str, int]:
    """Chuyển giá trị confidence thô (có thể thiếu/không hợp lệ) thành nhãn chuẩn kèm rank số."""
    confidence = str(value or "").strip().lower()
    if confidence in _CONFIDENCE_RANK:
        return confidence, _CONFIDENCE_RANK[confidence]
    # Một nguồn ngoài có thể chưa cung cấp confidence. Không loại bỏ dữ liệu
    # của nó, nhưng luôn ưu tiên high/medium đã được tính bởi extractor.
    return ("low", _CONFIDENCE_RANK["low"]) if has_value else ("missing", 0)


def _comparison_key(field: str, value: str) -> str:
    """So sánh các biểu diễn tương đương mà vẫn giữ nguyên giá trị tiếng Việt."""
    if field in _IDENTIFIER_FIELDS:
        return normalize_citizen_id(value) or normalize_text(value).casefold()
    if field in _PHONE_FIELDS:
        return normalize_phone(value) or normalize_text(value).casefold()
    if field in _DATE_FIELDS:
        return normalize_date(value) or normalize_text(value).casefold()
    return normalize_text(value).casefold()


def _accent_score(value: str) -> int:
    """Ưu tiên cách viết có dấu khi MRZ và mặt trước là cùng một họ tên."""
    decomposed = unicodedata.normalize("NFD", value)
    return sum(unicodedata.category(character) == "Mn" for character in decomposed) + value.count("Đ") + value.count("đ")


def _fold_name(value: str) -> str:
    """Chuẩn hóa tên về dạng không dấu, chữ thường để so sánh bỏ qua khác biệt dấu."""
    return strip_accents(normalize_text(value)).casefold()


def _same_name_variant(first: str, second: str) -> bool:
    """MRZ không dấu có thể đồng thuận với tên mặt trước, không che lỗi dấu."""
    if normalize_text(first).casefold() == normalize_text(second).casefold():
        return True
    return _fold_name(first) == _fold_name(second) and (
        _accent_score(first) == 0 or _accent_score(second) == 0
    )


def _group_candidates(field: str, candidates: list[_FieldValue]) -> list[list[_FieldValue]]:
    """Gom các ứng viên có giá trị tương đương thành từng nhóm để đếm mức đồng thuận.

    Trường thông thường gom theo comparison_key (so bằng tuyệt đối); riêng
    trường họ tên gom theo _same_name_variant để biến thể không dấu (MRZ)
    không bị tách ra thành một nhóm xung đột riêng.
    """
    if field not in _NAME_FIELDS:
        groups: dict[str, list[_FieldValue]] = {}
        for candidate in candidates:
            groups.setdefault(candidate.comparison_key, []).append(candidate)
        return list(groups.values())

    groups: list[list[_FieldValue]] = []
    for candidate in candidates:
        for group in groups:
            # Khi nhóm đã có tên có dấu, tên có dấu khác phải được xem là mâu
            # thuẫn; chỉ bản không dấu của MRZ mới được phép đồng thuận.
            references = [item for item in group if _accent_score(item.value) > 0] or group[:1]
            if any(_same_name_variant(candidate.value, item.value) for item in references):
                group.append(candidate)
                break
        else:
            groups.append([candidate])
    return groups


def _field_label(field: str) -> str:
    """Lấy nhãn tiếng Việt hiển thị cho một trường, fallback nếu chưa khai báo trong FIELD_NAMES."""
    # Không đưa khóa kỹ thuật tiếng Anh ra màn hình khi có nhãn biểu mẫu.
    return FIELD_NAMES.get(field, field.replace("_", " "))


def _is_regenerated_warning(warning: str) -> bool:
    """Bỏ cảnh báo theo từng tệp; chúng sẽ được tính lại sau khi gộp."""
    folded = warning.casefold()
    return any(
        marker in folded
        for marker in (
            "dữ liệu được suy luận từ ocr/pdf",
            "không nhận diện được số cccd/định danh",
            "không nhận diện được họ và tên đáng tin cậy",
        )
    )


def _deduplicate_warnings(results: list[ExtractionResult]) -> list[str]:
    """Gộp cảnh báo từ mọi tệp, loại trùng lặp và bỏ các cảnh báo theo-từng-tệp
    sẽ được tính lại dựa trên kết quả gộp cuối cùng (xem _is_regenerated_warning)."""
    warnings: list[str] = []
    seen: set[str] = set()
    for result in results:
        for warning in result.warnings:
            clean_warning = normalize_text(str(warning))
            key = clean_warning.casefold()
            if clean_warning and not _is_regenerated_warning(clean_warning) and key not in seen:
                warnings.append(clean_warning)
                seen.add(key)
    return warnings


def _select_value(field: str, candidates: list[_FieldValue]) -> tuple[_FieldValue, list[_FieldValue]]:
    """Chọn theo confidence, sau đó số tệp đồng thuận, rồi thứ tự tệp."""
    groups = _group_candidates(field, candidates)

    def group_sort_key(group: list[_FieldValue]) -> tuple[int, int, int]:
        return (
            max(item.rank for item in group),
            len(group),
            -min(item.source_index for item in group),
        )

    winning_group = max(groups, key=group_sort_key)
    selected = max(
        winning_group,
        key=lambda item: (item.rank, _accent_score(item.value) if field in _NAME_FIELDS else 0, -item.source_index),
    )
    alternatives = [item for group in groups if group is not winning_group for item in group]
    return selected, alternatives


def _profile_score(extraction: ExtractionResult, source_index: int) -> tuple[int, int, int]:
    """Rank the document most likely to represent the form applicant."""
    total_rank = 0
    populated = 0
    for field in (*_PROFILE_IDENTITY_FIELDS, "gender", "residence_address"):
        value = normalize_text(str(extraction.fields.get(field, "") or ""))
        if not value:
            continue
        _, rank = _normalise_confidence(extraction.confidence.get(field), has_value=True)
        total_rank += rank
        populated += 1
    return total_rank, populated, -source_index


def _profiles_conflict(primary: ExtractionResult, other: ExtractionResult) -> bool:
    """Xác định tệp `other` có vẻ thuộc về một người khác so với hồ sơ chính
    `primary` hay không, dựa trên bằng chứng mạnh nhất có sẵn: CCCD trước,
    rồi đến họ tên, rồi đến ngày sinh."""
    primary_id = normalize_citizen_id(str(primary.fields.get("citizen_id", "") or ""))
    other_id = normalize_citizen_id(str(other.fields.get("citizen_id", "") or ""))
    if primary_id and other_id:
        # Matching 12-digit identifiers are stronger evidence than a minor
        # OCR spelling/accent disagreement in the name.
        return primary_id != other_id

    primary_name = normalize_text(str(primary.fields.get("full_name", "") or ""))
    other_name = normalize_text(str(other.fields.get("full_name", "") or ""))
    if primary_name and other_name and not _same_name_variant(primary_name, other_name):
        return True

    primary_birth = normalize_date(str(primary.fields.get("date_of_birth", "") or ""))
    other_birth = normalize_date(str(other.fields.get("date_of_birth", "") or ""))
    return bool(primary_birth and other_birth and primary_birth != other_birth)


def merge_extraction_results(results: Iterable[ExtractionResult]) -> BatchExtractionResult:
    """Gộp ``ExtractionResult`` của nhiều tệp thành một hồ sơ để người dân duyệt.

    ``high`` được ưu tiên hơn ``medium``, ``low`` và ``missing``. Với cùng
    confidence, giá trị xuất hiện ở nhiều tệp hơn sẽ được chọn. Mọi giá trị
    không trống khác với giá trị được chọn đều được trả về trong ``conflicts``
    và kèm cảnh báo tiếng Việt trong ``result.warnings``.
    """
    result_list = list(results)
    # Lấy hợp của mọi trường chuẩn (FIELD_NAMES) và mọi trường phát sinh thêm
    # từ từng tệp, để không bỏ sót trường nào khi khởi tạo kết quả gộp.
    all_fields = set(FIELD_NAMES)
    for extraction in result_list:
        all_fields.update(extraction.fields)

    fields = {field: "" for field in all_fields}
    confidence = {field: "missing" for field in all_fields}
    warnings = _deduplicate_warnings(result_list)
    conflicts: list[FieldConflict] = []

    if not result_list:
        warnings.append("Chưa có tệp nào để trích xuất thông tin.")
        return BatchExtractionResult(
            result=ExtractionResult(fields=fields, confidence=confidence, warnings=warnings),
            conflicts=conflicts,
        )

    # Chọn tệp có hồ sơ định danh đầy đủ/tin cậy nhất làm "hồ sơ chính", sau đó
    # tìm các tệp khác có thông tin định danh mâu thuẫn với hồ sơ chính (khả
    # năng là giấy tờ của một người khác bị lẫn vào cùng lượt trích xuất).
    primary_index = max(range(len(result_list)), key=lambda index: _profile_score(result_list[index], index))
    primary_profile = result_list[primary_index]
    conflicting_source_indexes = {
        index
        for index, extraction in enumerate(result_list)
        if index != primary_index and _profiles_conflict(primary_profile, extraction)
    }
    if conflicting_source_indexes:
        warnings.append(
            "Phát hiện giấy tờ có thông tin định danh của người khác. "
            "Phần mềm ưu tiên hồ sơ định danh đáng tin cậy nhất và không dùng các trường bổ sung từ giấy tờ xung đột."
        )
        # Với riêng các trường định danh, ghi nhận xung đột giữa hồ sơ chính và
        # các tệp bị đánh dấu mâu thuẫn để người dùng biết và có thể kiểm tra lại.
        for field in _PROFILE_IDENTITY_FIELDS:
            selected_value = normalize_text(str(primary_profile.fields.get(field, "") or ""))
            if not selected_value:
                continue
            alternatives: list[str] = []
            source_indexes = {primary_index}
            selected_key = _comparison_key(field, selected_value)
            for source_index in sorted(conflicting_source_indexes):
                alternative = normalize_text(str(result_list[source_index].fields.get(field, "") or ""))
                if not alternative or _comparison_key(field, alternative) == selected_key:
                    continue
                if alternative not in alternatives:
                    alternatives.append(alternative)
                source_indexes.add(source_index)
            if not alternatives:
                continue
            label = _field_label(field)
            conflicts.append(
                FieldConflict(
                    field=field,
                    label=label,
                    selected_value=selected_value,
                    alternative_values=tuple(alternatives),
                    source_indexes=tuple(sorted(source_indexes)),
                )
            )
            warnings.append(
                f'Thông tin "{label}" không khớp giữa các tệp; '
                "đã giữ giá trị thuộc hồ sơ định danh chính. Vui lòng kiểm tra lại trước khi tạo đơn."
            )

    # Với mọi trường còn lại, gộp giá trị từ các tệp không bị coi là "người
    # khác" ở bước trên, ưu tiên theo confidence rồi đến mức đồng thuận giữa các tệp.
    for field in sorted(all_fields):
        candidates: list[_FieldValue] = []
        for source_index, extraction in enumerate(result_list):
            # Conflicts were recorded above. Never borrow any value, including
            # a higher-confidence identity number, from another person's file.
            if source_index in conflicting_source_indexes:
                continue
            value = normalize_text(str(extraction.fields.get(field, "") or ""))
            if not value:
                continue
            normalised_confidence, rank = _normalise_confidence(
                extraction.confidence.get(field), has_value=True
            )
            candidates.append(
                _FieldValue(
                    value=value,
                    comparison_key=_comparison_key(field, value),
                    confidence=normalised_confidence,
                    rank=rank,
                    source_index=source_index,
                )
            )

        if not candidates:
            continue

        selected, alternatives = _select_value(field, candidates)
        fields[field] = selected.value
        confidence[field] = selected.confidence

        if alternatives:
            alternative_values: list[str] = []
            seen_values: set[str] = set()
            for candidate in alternatives:
                if candidate.comparison_key not in seen_values:
                    alternative_values.append(candidate.value)
                    seen_values.add(candidate.comparison_key)
            label = _field_label(field)
            conflicts.append(
                FieldConflict(
                    field=field,
                    label=label,
                    selected_value=selected.value,
                    alternative_values=tuple(alternative_values),
                    source_indexes=tuple(sorted({item.source_index for item in candidates})),
                )
            )
            warnings.append(
                f'Thông tin "{label}" không khớp giữa các tệp; '
                "đã chọn giá trị có độ tin cậy hoặc mức đồng thuận cao hơn. "
                "Vui lòng kiểm tra lại trước khi tạo đơn."
            )

    # Cảnh báo của từng tệp có thể đã nói "thiếu CCCD" trong khi tệp khác đã
    # cung cấp trường đó. Chỉ sinh lại chúng từ kết quả gộp cuối cùng.
    warnings.insert(0, _GENERIC_OCR_WARNING)
    if not fields.get("citizen_id"):
        warnings.append(_MISSING_CITIZEN_ID_WARNING)
    if not fields.get("full_name"):
        warnings.append(_MISSING_FULL_NAME_WARNING)

    return BatchExtractionResult(
        result=ExtractionResult(fields=fields, confidence=confidence, warnings=warnings),
        conflicts=conflicts,
    )
