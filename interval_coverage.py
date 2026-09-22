"""Overlapping-interval coverage store, pure standard library."""

import json
import os


class InvalidInterval(ValueError):
    pass


class CoverageStore:
    """Piecewise coverage with per-piece origin and commit-gated persistence.

    Each piece carries the id of the write that created it. A new write
    replaces only the intersecting span; left/right remainders keep their
    original write id and pieces are never merged with neighbours, so
    endpoint-touching pieces stay distinct. A write fully covering a piece
    removes it (only the outer layer remains). With nested writes, only the
    top layer at the intersecting span is cut; inner layers elsewhere are
    untouched.

    Each write is appended to a log file as a record plus a COMMIT marker,
    then fsynced. On reopen only committed records are replayed; a torn tail
    record is dropped and never joined with anything after it.
    """

    def __init__(self, path):
        self._path = path
        self._pieces = []
        self._seen = set()
        self._next_id = 0
        self._valid_size = 0
        self._load()

    def _load(self):
        if not os.path.exists(self._path):
            return
        with open(self._path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines(keepends=True)
        idx = 0
        while idx + 1 < len(lines):
            try:
                record = json.loads(lines[idx].strip())
            except ValueError:
                break
            if lines[idx + 1].strip() != "COMMIT":
                break
            if (
                not isinstance(record, list)
                or len(record) != 3
                or record[0] != "W"
                or not all(isinstance(v, int) for v in record[1:])
            ):
                break
            self._apply(record[1], record[2])
            idx += 2
        self._valid_size = sum(len(line.encode("utf-8")) for line in lines[:idx])

    def write(self, start, end):
        if not isinstance(start, int) or not isinstance(end, int):
            raise InvalidInterval("bounds must be ints")
        if start >= end:
            raise InvalidInterval("empty or reversed interval: %r..%r" % (start, end))
        if (start, end) in self._seen:
            return
        with open(self._path, "a+", encoding="utf-8") as fh:
            fh.truncate(self._valid_size)  # drop any torn tail before appending
            fh.write(json.dumps(["W", start, end]) + "\n")
            fh.write("COMMIT\n")
            fh.flush()
            os.fsync(fh.fileno())
            self._valid_size = fh.tell()
        self._apply(start, end)

    def _apply(self, start, end):
        origin = self._next_id
        self._next_id += 1
        self._seen.add((start, end))
        kept = []
        for p_start, p_end, p_origin in self._pieces:
            if p_end <= start or p_start >= end:
                kept.append([p_start, p_end, p_origin])
                continue
            if p_start < start:
                kept.append([p_start, start, p_origin])
            if p_end > end:
                kept.append([end, p_end, p_origin])
        kept.append([start, end, origin])
        kept.sort(key=lambda p: (p[0], p[1]))
        self._pieces = kept

    def pieces(self):
        return [tuple(p) for p in self._pieces]

    def spans(self):
        return [(p[0], p[1]) for p in self._pieces]
