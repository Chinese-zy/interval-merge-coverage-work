import json
import os
import struct
import tempfile
import unittest
import zlib

from coverage_store import CoverageStore


def frame(start: int, end: int) -> bytes:
    payload = json.dumps(
        {"start": start, "end": end},
        separators=(",", ":"),
    ).encode("utf-8")
    return struct.pack(">I", len(payload)) + payload + struct.pack(">I", zlib.crc32(payload))


class CoverageStoreTest(unittest.TestCase):
    def test_fixed_overlap_cases(self) -> None:
        cases = [
            (
                "端点相碰不合并",
                [(0, 10), (10, 20)],
                [(0, 10, 1), (10, 20, 2)],
            ),
            (
                "只覆盖中间相交部分",
                [(0, 20), (8, 12)],
                [(0, 8, 1), (8, 12, 2), (12, 20, 1)],
            ),
            (
                "残余保留来源且不并回",
                [(0, 20), (5, 15), (7, 9)],
                [
                    (0, 5, 1),
                    (5, 7, 2),
                    (7, 9, 3),
                    (9, 15, 2),
                    (15, 20, 1),
                ],
            ),
            (
                "完整覆盖只留外层",
                [(5, 15), (0, 20)],
                [(0, 20, 2)],
            ),
            (
                "三层嵌套只削触达层",
                [(0, 100), (20, 80), (30, 40)],
                [
                    (0, 20, 1),
                    (20, 30, 2),
                    (30, 40, 3),
                    (40, 80, 2),
                    (80, 100, 1),
                ],
            ),
        ]

        for name, writes, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                with CoverageStore(os.path.join(directory, "coverage.log")) as store:
                    for start, end in writes:
                        self.assertTrue(store.write(start, end))
                    actual = [segment.as_tuple() for segment in store.segments]
                self.assertEqual(actual, expected)

    def test_exact_duplicate_is_ignored_but_endpoint_change_is_new(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "coverage.log")
            with CoverageStore(path) as store:
                self.assertTrue(store.write(10, 30))
                self.assertFalse(store.write(10, 30))
                self.assertTrue(store.write(10, 29))
                self.assertEqual(
                    [segment.as_tuple() for segment in store.segments],
                    [(10, 29, 2), (29, 30, 1)],
                )

            with CoverageStore(path) as reopened:
                self.assertEqual(
                    [segment.as_tuple() for segment in reopened.segments],
                    [(10, 29, 2), (29, 30, 1)],
                )

    def test_zero_length_and_reversed_intervals_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "coverage.log")
            with CoverageStore(path) as store:
                with self.assertRaises(ValueError):
                    store.write(7, 7)
                with self.assertRaises(ValueError):
                    store.write(9, 2)
                self.assertEqual(store.segments, ())

            with open(path, "rb") as journal:
                self.assertEqual(journal.read(), b"")

    def test_reopening_discards_half_written_record_and_keeps_complete_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "coverage.log")
            with open(path, "wb") as journal:
                journal.write(frame(2, 18))
                journal.write(struct.pack(">I", 2))
                journal.write(b"{")
                journal.flush()

            with CoverageStore(path) as reopened:
                self.assertEqual(
                    [segment.as_tuple() for segment in reopened.segments],
                    [(2, 18, 1)],
                )
                reopened.write(20, 40)

            with open(path, "rb") as journal:
                contents = journal.read()
            self.assertEqual(contents, frame(2, 18) + frame(20, 40))


if __name__ == "__main__":
    unittest.main()
