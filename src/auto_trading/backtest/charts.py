"""외부 plotting 의존성 없이 결정론적 PNG 차트를 만드는 작은 렌더러."""

from __future__ import annotations

import binascii
import math
import struct
import zlib
from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path

__all__ = ["write_bar_chart", "write_histogram", "write_line_chart", "write_scatter"]

_WIDTH = 960
_HEIGHT = 540
_LEFT = 60
_RIGHT = 24
_TOP = 24
_BOTTOM = 44
_WHITE = (255, 255, 255)
_GRID = (225, 230, 235)
_AXIS = (80, 88, 96)
_BLUE = (35, 113, 184)
_RED = (196, 62, 62)
_GREEN = (36, 145, 92)


class _Canvas:
    def __init__(self, width: int = _WIDTH, height: int = _HEIGHT) -> None:
        self.width = width
        self.height = height
        self.pixels = bytearray(_WHITE * (width * height))

    def pixel(self, x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            offset = (y * self.width + x) * 3
            self.pixels[offset : offset + 3] = bytes(color)

    def line(self, x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int]) -> None:
        dx = abs(x1 - x0)
        sx = 1 if x0 < x1 else -1
        dy = -abs(y1 - y0)
        sy = 1 if y0 < y1 else -1
        error = dx + dy
        while True:
            self.pixel(x0, y0, color)
            if x0 == x1 and y0 == y1:
                break
            twice = 2 * error
            if twice >= dy:
                error += dy
                x0 += sx
            if twice <= dx:
                error += dx
                y0 += sy

    def rectangle(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        color: tuple[int, int, int],
    ) -> None:
        left, right = sorted((max(0, x0), min(self.width - 1, x1)))
        top, bottom = sorted((max(0, y0), min(self.height - 1, y1)))
        row = bytes(color) * (right - left + 1)
        for y in range(top, bottom + 1):
            offset = (y * self.width + left) * 3
            self.pixels[offset : offset + len(row)] = row

    def circle(self, x: int, y: int, radius: int, color: tuple[int, int, int]) -> None:
        for dy in range(-radius, radius + 1):
            span = math.isqrt(max(0, radius * radius - dy * dy))
            self.rectangle(x - span, y + dy, x + span, y + dy, color)

    def save(self, path: Path) -> None:
        stride = self.width * 3
        raw = b"".join(
            b"\x00" + self.pixels[row * stride : (row + 1) * stride] for row in range(self.height)
        )
        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        payload = b"\x89PNG\r\n\x1a\n"
        payload += _chunk(b"IHDR", header)
        payload += _chunk(b"IDAT", zlib.compress(raw, level=9))
        payload += _chunk(b"IEND", b"")
        path.write_bytes(payload)


def write_line_chart(
    path: Path,
    values: Sequence[float],
    *,
    color: tuple[int, int, int] = _BLUE,
    zero_line: bool = False,
) -> None:
    canvas = _Canvas()
    _grid(canvas)
    if values:
        minimum, maximum = _bounds(values, include_zero=zero_line)
        points = [
            (
                _scale_index(index, len(values)),
                _scale_value(value, minimum, maximum),
            )
            for index, value in enumerate(values)
        ]
        for first, second in pairwise(points):
            canvas.line(*first, *second, color)
        if len(points) == 1:
            canvas.circle(*points[0], 2, color)
        if zero_line and minimum <= 0.0 <= maximum:
            y_zero = _scale_value(0.0, minimum, maximum)
            canvas.line(_LEFT, y_zero, _WIDTH - _RIGHT, y_zero, _AXIS)
    canvas.save(path)


def write_bar_chart(path: Path, values: Sequence[float]) -> None:
    canvas = _Canvas()
    _grid(canvas)
    if values:
        minimum, maximum = _bounds(values, include_zero=True)
        zero_y = _scale_value(0.0, minimum, maximum)
        plot_width = _WIDTH - _LEFT - _RIGHT
        slot = plot_width / len(values)
        for index, value in enumerate(values):
            center = _LEFT + (index + 0.5) * slot
            half = max(1, int(slot * 0.35))
            value_y = _scale_value(value, minimum, maximum)
            color = _GREEN if value >= 0.0 else _RED
            canvas.rectangle(
                round(center) - half,
                min(zero_y, value_y),
                round(center) + half,
                max(zero_y, value_y),
                color,
            )
        canvas.line(_LEFT, zero_y, _WIDTH - _RIGHT, zero_y, _AXIS)
    canvas.save(path)


def write_histogram(path: Path, values: Sequence[float], *, bins: int = 20) -> None:
    if bins < 1:
        raise ValueError(f"bins 는 1 이상이어야 한다: {bins}")
    if not values:
        write_bar_chart(path, ())
        return
    minimum = min(values)
    maximum = max(values)
    counts = [0.0] * bins
    if minimum == maximum:
        counts[bins // 2] = float(len(values))
    else:
        width = (maximum - minimum) / bins
        for value in values:
            index = min(bins - 1, int((value - minimum) / width))
            counts[index] += 1.0
    write_bar_chart(path, counts)


def write_scatter(path: Path, points: Sequence[tuple[float, float]]) -> None:
    canvas = _Canvas()
    _grid(canvas)
    if points:
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        x_min, x_max = _bounds(xs, include_zero=True)
        y_min, y_max = _bounds(ys, include_zero=True)
        for x_value, y_value in points:
            x = _scale(x_value, x_min, x_max, _LEFT, _WIDTH - _RIGHT)
            y = _scale(y_value, y_min, y_max, _HEIGHT - _BOTTOM, _TOP)
            canvas.circle(x, y, 3, _BLUE)
    canvas.save(path)


def _grid(canvas: _Canvas) -> None:
    for index in range(6):
        y = _TOP + index * (_HEIGHT - _TOP - _BOTTOM) // 5
        canvas.line(_LEFT, y, _WIDTH - _RIGHT, y, _GRID)
    canvas.line(_LEFT, _TOP, _LEFT, _HEIGHT - _BOTTOM, _AXIS)
    canvas.line(_LEFT, _HEIGHT - _BOTTOM, _WIDTH - _RIGHT, _HEIGHT - _BOTTOM, _AXIS)


def _bounds(values: Sequence[float], *, include_zero: bool) -> tuple[float, float]:
    minimum = min(values)
    maximum = max(values)
    if include_zero:
        minimum = min(minimum, 0.0)
        maximum = max(maximum, 0.0)
    if minimum == maximum:
        padding = abs(minimum) * 0.05 or 1.0
        return minimum - padding, maximum + padding
    padding = (maximum - minimum) * 0.05
    return minimum - padding, maximum + padding


def _scale_index(index: int, count: int) -> int:
    if count <= 1:
        return (_LEFT + _WIDTH - _RIGHT) // 2
    return round(_LEFT + index * (_WIDTH - _LEFT - _RIGHT) / (count - 1))


def _scale_value(value: float, minimum: float, maximum: float) -> int:
    return _scale(value, minimum, maximum, _HEIGHT - _BOTTOM, _TOP)


def _scale(value: float, low: float, high: float, start: int, end: int) -> int:
    if high == low:
        return (start + end) // 2
    return round(start + (value - low) / (high - low) * (end - start))


def _chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", binascii.crc32(body))
