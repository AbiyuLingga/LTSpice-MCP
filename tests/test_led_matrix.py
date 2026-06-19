"""Tests for the deterministic Tiny8 LED matrix simulator."""

from __future__ import annotations

from ltagent.digital_asm import assemble_program
from ltagent.digital_emulator import run_program
from ltagent.led_matrix import LedMatrix, render_tiny8_led_frames


def _words(source: str) -> list[int]:
    return assemble_program(source, rom_size=32).words


def test_led_matrix_commits_a_pixel_frame_from_tiny8_out_ports() -> None:
    result = run_program(
        _words(
            """
            LDI 2
            OUT 0xF0
            LDI 3
            OUT 0xF1
            LDI 1
            OUT 0xF2
            OUT 0xF4
            HALT
            """
        ),
        max_cycles=16,
    )

    rendered = render_tiny8_led_frames(result.output_events)

    assert rendered.diagnostics == ()
    assert len(rendered.frames) == 1
    assert rendered.frames[0].pixel(2, 3) is True
    assert rendered.frames[0].cycle == 7


def test_led_matrix_refuses_out_of_range_pixels_without_corrupting_frame() -> None:
    matrix = LedMatrix(width=8, height=16)
    matrix.write_port(0xF0, 8, cycle=1)
    matrix.write_port(0xF1, 0, cycle=2)
    matrix.write_port(0xF2, 1, cycle=3)
    matrix.write_port(0xF4, 1, cycle=4)

    rendered = matrix.rendered()

    assert len(rendered.frames) == 1
    assert not any(rendered.frames[0].pixels)
    assert rendered.diagnostics[0].code == "LED_COORDINATE_OUT_OF_RANGE"
