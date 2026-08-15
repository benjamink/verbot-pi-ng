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

from kicadgen import (
    GRID,
    Pin,
    assert_grid,
    extract_symbol,
    grid_out,
    lib_symbol,
    on_grid,
    pin_positions,
    property_placements,
)


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


def test_grid_out_snaps_away_from_zero():
    assert grid_out(2.032) == 2.54, grid_out(2.032)
    assert grid_out(-1.524) == -2.54, grid_out(-1.524)
    assert grid_out(0) == 0.0
    assert grid_out(11.43) == 11.43, "already on grid, must not move"


def test_property_placements_come_from_the_library():
    # Device:R parks Value at the origin - the case generate_schematic.py has
    # to override - while Motor_DC puts both fields clear of the body.
    assert property_placements("Device", "R")["Value"].dx == 0.0
    motor = property_placements("Motor", "Motor_DC")
    assert motor["Reference"].dy == 2.54, motor["Reference"]
    assert motor["Value"].dy == -5.08, motor["Value"]
    assert motor["Value"].justify == "left top", motor["Value"]


def test_block_symbols_place_their_text_outside_the_body():
    """The whole point of reading the library: no Value inside a pin field."""
    for name, half_height in (
        ("DRV8833_Carrier", 8.89),
        ("MAX98357A_Breakout", 7.62),
        ("MCP23017_Breakout", 21.59),
        ("OnOff_SHIM", 6.35),
        ("Verbot_Gearbox", 12.7),
    ):
        places = _project_placements(name)
        for kind, place in places.items():
            if kind not in ("Reference", "Value"):
                continue
            assert abs(place.dy) > half_height, f"{name} {kind} lands inside the block"
            assert abs(place.dy / GRID - round(place.dy / GRID)) < 1e-6, f"{name} {kind} off grid"


def _project_placements(name):
    import kicadgen

    original = kicadgen.SYMBOL_DIR
    kicadgen.SYMBOL_DIR = ".."
    try:
        return kicadgen.property_placements("verbot", name)
    finally:
        kicadgen.SYMBOL_DIR = original


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
