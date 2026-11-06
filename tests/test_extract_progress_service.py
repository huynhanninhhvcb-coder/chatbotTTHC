"""Kiểm thử bộ theo dõi tiến trình xử lý batch /api/extract trong RAM."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from services.extract_progress_service import ExtractProgressTracker


class ExtractProgressTrackerTests(unittest.TestCase):
    def test_advance_increments_done_count(self):
        tracker = ExtractProgressTracker()
        tracker.start("batch-1", total=3)

        tracker.advance("batch-1")
        tracker.advance("batch-1")

        self.assertEqual(tracker.get("batch-1"), {"done": 2, "total": 3})

    def test_finish_removes_the_batch(self):
        tracker = ExtractProgressTracker()
        tracker.start("batch-1", total=2)
        tracker.advance("batch-1")

        tracker.finish("batch-1")

        self.assertEqual(tracker.get("batch-1"), {"done": 0, "total": 0})

    def test_unknown_batch_returns_zeros_instead_of_raising(self):
        tracker = ExtractProgressTracker()
        self.assertEqual(tracker.get("khong-ton-tai"), {"done": 0, "total": 0})

    def test_advance_on_unknown_batch_is_a_no_op(self):
        tracker = ExtractProgressTracker()
        tracker.advance("khong-ton-tai")  # không được ném lỗi
        self.assertEqual(tracker.get("khong-ton-tai"), {"done": 0, "total": 0})

    def test_stale_batches_are_pruned_on_the_next_start(self):
        tracker = ExtractProgressTracker()
        with patch("services.extract_progress_service.time.monotonic", side_effect=[0.0, 0.0]):
            tracker.start("old-batch", total=1)
        with patch("services.extract_progress_service.time.monotonic", return_value=10 * 60):
            tracker.start("new-batch", total=1)

        self.assertEqual(tracker.get("old-batch"), {"done": 0, "total": 0})
        self.assertEqual(tracker.get("new-batch"), {"done": 0, "total": 1})


if __name__ == "__main__":
    unittest.main()
