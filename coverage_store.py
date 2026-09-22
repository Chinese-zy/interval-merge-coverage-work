from __future__ import annotations

import json
import os
import struct
import zlib
from dataclasses import dataclass
from typing import BinaryIO


@dataclass(frozen=True, slots=True)
class Segment:
    start: int
    end: int
    source: int

    def as_tuple(self) -> tuple[int, int, int]:
        return (self.start, self.end, self.source)


class CoverageStore:
    def __init__(self, path: str):
        self.path = path
        self._segments: list[Segment] = []
        self._written: set[tuple[int, int]] = set()
        self._next_source = 1
        self._file: BinaryIO | None = None
        self._reopen()

    @property
    def segments(self) -> tuple[Segment, ...]:
        return tuple(self._segments)

    def write(self, start: int, end: int) -> bool:
        self._validate(start, end)
        key = (start, end)
        if key in self._written:
            return False

        source = self._next_source
        self._append_record(start, end)
        self._written.add(key)
        self._next_source += 1
        self._segments = overlay(self._segments, start, end, source)
        return True

    def close(self) -> None:
        if self._file is not None:
            self._file.flush()
            os.fsync(self._file.fileno())
            self._file.close()
            self._file = None

    def __enter__(self) -> CoverageStore:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _reopen(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        valid_length = self._recover()
        self._file = open(self.path, "r+b" if os.path.exists(self.path) else "w+b")
        self._file.seek(0, os.SEEK_END)
        if self._file.tell() != valid_length:
            self._file.seek(valid_length)
            self._file.truncate()

    def _recover(self) -> int:
        if not os.path.exists(self.path):
            return 0

        offset = 0
        with open(self.path, "rb") as journal:
            while True:
                header = journal.read(4)
                if not header:
                    break
                if len(header) != 4:
                    break

                payload_length = struct.unpack(">I", header)[0]
                frame_start = offset
                payload = journal.read(payload_length)
                checksum_bytes = journal.read(4)
                if len(payload) != payload_length or len(checksum_bytes) != 4:
                    break

                expected = struct.unpack(">I", checksum_bytes)[0]
                actual = zlib.crc32(payload)
                if expected != actual:
                    break

                try:
                    record = json.loads(payload.decode("utf-8"))
                    start = record["start"]
                    end = record["end"]
                except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
                    break

                if not self._replay(start, end):
                    break

                offset = frame_start + 4 + payload_length + 4

        return offset

    def _replay(self, start: object, end: object) -> bool:
        if not isinstance(start, int) or not isinstance(end, int):
            return False
        try:
            self._validate(start, end)
        except (TypeError, ValueError):
            return False

        key = (start, end)
        if key in self._written:
            return False

        source = self._next_source
        self._written.add(key)
        self._next_source += 1
        self._segments = overlay(self._segments, start, end, source)
        return True

    def _append_record(self, start: int, end: int) -> None:
        if self._file is None:
            raise RuntimeError("coverage store is closed")

        payload = json.dumps(
            {"start": start, "end": end},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        frame = (
            struct.pack(">I", len(payload))
            + payload
            + struct.pack(">I", zlib.crc32(payload))
        )
        self._file.write(frame)
        self._file.flush()
        os.fsync(self._file.fileno())

    @staticmethod
    def _validate(start: int, end: int) -> None:
        if type(start) is not int or type(end) is not int:
            raise TypeError("start and end must be integers")
        if start >= end:
            raise ValueError("start must be strictly less than end")


def overlay(
    segments: tuple[Segment, ...] | list[Segment],
    start: int,
    end: int,
    source: int,
) -> list[Segment]:
    result: list[Segment] = []

    for segment in segments:
        if end <= segment.start or start >= segment.end:
            result.append(segment)
            continue

        if segment.start < start:
            result.append(Segment(segment.start, min(segment.end, start), segment.source))
        if end < segment.end:
            result.append(Segment(max(segment.start, end), segment.end, segment.source))

    result.append(Segment(start, end, source))
    result.sort(key=lambda item: (item.start, item.end, item.source))
    return result
