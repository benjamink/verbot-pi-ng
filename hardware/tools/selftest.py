"""Self-checks for the schematic generator. Run: python3 hardware/tools/selftest.py

Deliberately not a pytest module, even though this repo uses pytest
everywhere (see `testpaths = ["tests"]` in pyproject.toml). These checks read
symbol geometry out of /usr/share/kicad/symbols, which only exists on a
machine with KiCad installed. Folding them into the pytest suite would break
the README's promise that `uv run pytest` runs clean on any machine with no
special setup - a checkout without KiCad would fail tests it has no way to
satisfy. Keeping this script standalone and outside tests/ keeps that promise
intact.
"""

import sys

from kicadgen import Pin, assert_grid, extract_symbol, lib_symbol, on_grid, pin_positions


def test_extract_symbol_is_balanced():
    block = extract_symbol("Device", "R")
    assert block.startswith('(symbol "R"'), block[:40]
    assert block.count("(") == block.count(")"), "unbalanced parens"


def test_lib_symbol_is_rekeyed():
    block = lib_symbol("Device", "R")
    assert block.startswith('(symbol "Device:R"'), block[:40]


def test_pin_positions_reads_all_pi_header_pins():
    pins = pin_positions("Connector", "Raspberry_Pi_2_3")
    assert len(pins) == 40, len(pins)
    assert pins["1"] == Pin(5.08, 33.02, 270, "3V3")
    assert pins["32"].x == 20.32 and pins["32"].y == -17.78


def test_pi_header_pins_are_all_on_grid():
    pins = pin_positions("Connector", "Raspberry_Pi_2_3")
    off = [n for n, p in pins.items() if on_grid(p.x) != p.x or on_grid(p.y) != p.y]
    assert off == [], off


def test_assert_grid_rejects_off_grid_points():
    assert_grid((101.6, 97.79))
    try:
        assert_grid((100.0, 100.0))
    except ValueError:
        return
    raise AssertionError("assert_grid accepted an off-grid point")


def test_project_library_has_all_five_symbols():
    text = open("../verbot.kicad_sym").read()
    for name in (
        "DRV8833_Carrier",
        "MAX98357A_Breakout",
        "MCP23017_Breakout",
        "OnOff_SHIM",
        "Verbot_Gearbox",
    ):
        assert f'(symbol "{name}"' in text, f"missing symbol {name}"


def test_project_library_is_balanced():
    text = open("../verbot.kicad_sym").read()
    assert text.count("(") == text.count(")"), "unbalanced parens"


def test_gearbox_names_pins_by_wire_colour():
    text = open("../verbot.kicad_sym").read()
    for colour in ("PURPLE", "RED", "YELLOW", "GREY", "BLUE", "BROWN", "ORANGE", "GREEN"):
        assert f'(name "{colour}"' in text, f"missing gearbox pin {colour}"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    sys.exit(1 if failures else 0)
