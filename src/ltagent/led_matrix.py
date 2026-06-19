"""Tiny8 memory-mapped 8x16 LED matrix simulator.

The renderer consumes deterministic emulator OUT events. It is deliberately
separate from the CPU so it can also render HDL trace adapters later.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from .digital_emulator import OutputEvent

PORT_LED_X: Final[int] = 0xF0
PORT_LED_Y: Final[int] = 0xF1
PORT_LED_VALUE: Final[int] = 0xF2
PORT_FRAME_COMMIT: Final[int] = 0xF4


@dataclass(frozen=True)
class LedFrame:
    """One immutable LED matrix frame committed by Tiny8 firmware."""

    cycle: int
    width: int
    height: int
    pixels: tuple[bool, ...]

    def pixel(self, x: int, y: int) -> bool:
        if not 0 <= x < self.width or not 0 <= y < self.height:
            raise ValueError("pixel coordinate is outside the LED matrix")
        return self.pixels[y * self.width + x]


@dataclass(frozen=True)
class LedDiagnostic:
    code: str
    cycle: int
    x: int
    y: int


@dataclass(frozen=True)
class LedRenderResult:
    width: int
    height: int
    frames: tuple[LedFrame, ...]
    diagnostics: tuple[LedDiagnostic, ...]


class LedMatrix:
    """Stateful LED peripheral addressed through Tiny8 OUT ports."""

    def __init__(self, *, width: int = 8, height: int = 16) -> None:
        if width < 1 or height < 1:
            raise ValueError("LED matrix width and height must be positive")
        self.width = width
        self.height = height
        self._x = 0
        self._y = 0
        self._pixels = [False] * (width * height)
        self._frames: list[LedFrame] = []
        self._diagnostics: list[LedDiagnostic] = []

    def write_port(self, port: int, data: int, *, cycle: int) -> None:
        """Apply one CPU OUT event to the peripheral state."""
        if not 0 <= port <= 0xFF or not 0 <= data <= 0xFF or cycle < 0:
            raise ValueError("port, data, and cycle are outside their valid ranges")
        if port == PORT_LED_X:
            self._x = data
        elif port == PORT_LED_Y:
            self._y = data
        elif port == PORT_LED_VALUE:
            self._write_pixel(data != 0, cycle=cycle)
        elif port == PORT_FRAME_COMMIT:
            self._frames.append(
                LedFrame(
                    cycle=cycle,
                    width=self.width,
                    height=self.height,
                    pixels=tuple(self._pixels),
                )
            )

    def rendered(self) -> LedRenderResult:
        return LedRenderResult(
            width=self.width,
            height=self.height,
            frames=tuple(self._frames),
            diagnostics=tuple(self._diagnostics),
        )

    def _write_pixel(self, value: bool, *, cycle: int) -> None:
        if not 0 <= self._x < self.width or not 0 <= self._y < self.height:
            self._diagnostics.append(
                LedDiagnostic(
                    code="LED_COORDINATE_OUT_OF_RANGE",
                    cycle=cycle,
                    x=self._x,
                    y=self._y,
                )
            )
            return
        self._pixels[self._y * self.width + self._x] = value


def render_tiny8_led_frames(
    output_events: Iterable[OutputEvent],
    *,
    width: int = 8,
    height: int = 16,
) -> LedRenderResult:
    """Render ordered Tiny8 OUT events into committed LED frames."""
    matrix = LedMatrix(width=width, height=height)
    for event in output_events:
        matrix.write_port(event.port, event.data, cycle=event.cycle)
    return matrix.rendered()


__all__ = [
    "PORT_FRAME_COMMIT",
    "PORT_LED_VALUE",
    "PORT_LED_X",
    "PORT_LED_Y",
    "LedDiagnostic",
    "LedFrame",
    "LedMatrix",
    "LedRenderResult",
    "render_tiny8_led_frames",
]
