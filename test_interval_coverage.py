import os
import tempfile
import unittest

from interval_coverage import CoverageStore, InvalidInterval


class CoverageTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp()
        os.close(fd)
        os.unlink(self.path)
        self.store = CoverageStore(self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_endpoint_touching_does_not_merge(self):
        self.store.write(0, 5)
        self.store.write(5, 10)
        self.assertEqual(self.store.spans(), [(0, 5), (5, 10)])
        origins = [o for _, _, o in self.store.pieces()]
        self.assertEqual(len(set(origins)), 2)

    def test_partial_overlap_keeps_remainders_with_origin(self):
        self.store.write(0, 10)
        self.store.write(4, 6)
        pieces = self.store.pieces()
        self.assertEqual([(s, e) for s, e, _ in pieces], [(0, 4), (4, 6), (6, 10)])
        self.assertEqual(pieces[0][2], pieces[2][2])  # same original write
        self.assertNotEqual(pieces[0][2], pieces[1][2])

    def test_remainders_not_merged_back_into_neighbour(self):
        self.store.write(0, 10)
        self.store.write(4, 6)
        self.store.write(4, 6)  # dedup no-op
        self.store.write(3, 7)  # covers the middle piece entirely
        pieces = self.store.pieces()
        self.assertEqual([(s, e) for s, e, _ in pieces], [(0, 3), (3, 7), (7, 10)])
        self.assertEqual(pieces[0][2], pieces[2][2])
        self.assertNotEqual(pieces[0][2], pieces[1][2])

    def test_full_cover_leaves_only_outer_layer(self):
        self.store.write(0, 10)
        self.store.write(2, 8)
        self.store.write(0, 10)  # dedup no-op, same bounds
        self.store.write(1, 9)   # swallows the inner layer whole
        spans = self.store.spans()
        self.assertEqual(spans, [(0, 1), (1, 9), (9, 10)])
        # inner layer (2,8) must not reappear through any gap
        self.store.write(3, 5)
        self.assertEqual(self.store.spans(), [(0, 1), (1, 3), (3, 5), (5, 9), (9, 10)])

    def test_nested_three_layers_cuts_only_touched_layer(self):
        self.store.write(0, 10)   # id 0
        self.store.write(2, 8)    # id 1
        self.store.write(4, 6)    # id 2
        self.store.write(5, 7)    # cuts layer id 1 at 5..6 and id 2 at 5..6
        pieces = self.store.pieces()
        self.assertEqual(
            [(s, e) for s, e, _ in pieces],
            [(0, 2), (2, 4), (4, 5), (5, 7), (7, 8), (8, 10)],
        )
        by_span = {(s, e): o for s, e, o in pieces}
        self.assertEqual(by_span[(4, 5)], 2)  # innermost layer intact left of cut
        self.assertEqual(by_span[(7, 8)], 1)  # middle layer intact right of cut
        self.assertEqual(by_span[(0, 2)], 0)
        self.assertEqual(by_span[(8, 10)], 0)

    def test_dedup_same_bounds_but_not_off_by_one(self):
        self.store.write(2, 8)
        self.store.write(2, 8)
        self.assertEqual(self.store.spans(), [(2, 8)])
        with open(self.path) as fh:
            self.assertEqual(fh.read().count("COMMIT"), 1)
        self.store.write(2, 9)  # one endpoint differs: a different write
        self.assertEqual(self.store.spans(), [(2, 9)])
        self.store.write(3, 9)
        self.assertEqual(self.store.spans(), [(2, 3), (3, 9)])

    def test_rejects_zero_length_and_reversed(self):
        for bad in [(5, 5), (9, 4), (0, 0)]:
            with self.assertRaises(InvalidInterval):
                self.store.write(*bad)
        self.assertEqual(self.store.spans(), [])
        self.assertFalse(os.path.exists(self.path))

    def test_reopen_replays_committed_only(self):
        self.store.write(0, 10)
        self.store.write(20, 30)
        reopened = CoverageStore(self.path)
        self.assertEqual(reopened.spans(), [(0, 10), (20, 30)])

    def test_torn_tail_record_is_dropped(self):
        self.store.write(0, 10)
        self.store.write(20, 30)
        with open(self.path, "a") as fh:
            fh.write('["W", 40, 50]\n')  # record without COMMIT: crashed mid-write
        reopened = CoverageStore(self.path)
        self.assertEqual(reopened.spans(), [(0, 10), (20, 30)])

    def test_truncated_record_bytes_are_dropped(self):
        self.store.write(0, 10)
        with open(self.path, "a") as fh:
            fh.write('["W", 40, 5')  # torn bytes, no newline, no commit
        reopened = CoverageStore(self.path)
        self.assertEqual(reopened.spans(), [(0, 10)])
        # store still usable afterwards; new write does not join the torn half
        reopened.write(50, 60)
        again = CoverageStore(self.path)
        self.assertEqual(again.spans(), [(0, 10), (50, 60)])

    def test_fixed_table_scenario(self):
        table = [
            ((10, 20), [(10, 20)]),
            ((30, 40), [(10, 20), (30, 40)]),
            ((15, 35), [(10, 15), (15, 35), (35, 40)]),
            ((0, 5), [(0, 5), (10, 15), (15, 35), (35, 40)]),
            ((12, 18), [(0, 5), (10, 12), (12, 18), (18, 35), (35, 40)]),
        ]
        for (start, end), expected in table:
            self.store.write(start, end)
            self.assertEqual(self.store.spans(), expected)
        reopened = CoverageStore(self.path)
        self.assertEqual(reopened.spans(), table[-1][1])


if __name__ == "__main__":
    unittest.main()
