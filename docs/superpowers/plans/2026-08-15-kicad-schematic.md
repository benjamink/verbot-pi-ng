# KiCad Wiring Schematic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a single-sheet A3 KiCad schematic documenting how every part of the robot wires to the Raspberry Pi Zero 2 W, ERC-clean and exported to committed PDF/SVG.

**Architecture:** A one-shot Python generator assembles the `.kicad_sch` S-expression, reading symbol geometry directly out of the KiCad stock libraries so pin coordinates are never hand-computed. Module blocks that have no stock equivalent live in a project symbol library. Once generated and committed, the `.kicad_sch` is the authoritative artifact and is edited in eeschema like any normal project — the generator is scaffolding, not a maintained pipeline.

**Tech Stack:** KiCad 10.0.5 (`kicad-cli`), Python 3.13 stdlib only (no new project dependencies).

Spec: `docs/superpowers/specs/2026-08-15-kicad-schematic-design.md`

## Global Constraints

- Schematic file format: `(version 20260306)`, `(generator_version "10.0")`. Symbol library format: `(version 20251024)`.
- **Every coordinate must be an exact multiple of 1.27mm.** Off-grid endpoints produce `endpoint_off_grid` ERC warnings. This was verified empirically — it is the single most common failure mode when writing these files by hand.
- Paper size: `A3` (420 × 297 mm).
- `lib_symbols` must embed a full copy of every symbol used, re-keyed from `(symbol "NAME"` to `(symbol "Lib:NAME"`.
- Every externally-driven power net (`+5V`, `+3V3`, `GND`) needs a `power:PWR_FLAG` connected to it, or ERC raises `power_pin_not_driven` as an **error**.
- Every `no_connect` flag must sit exactly on a pin coordinate, or ERC raises `no_connect_dangling` as a warning.
- **Line breaks inside a `text_note` body must be written `\n` in the Python source** (a literal backslash-n), so the emitted file carries the two-character `\n` escape KiCad expects inside a quoted string. A real newline character inside a quoted S-expression string makes the file unparseable — `kicad-cli` reports `Failed to load schematic`, verified empirically. This applies only to string *contents*; joins that separate top-level blocks (`lib_cache`, `BODY`) need real newlines.
- Acceptance gate for every task from Task 3 onward: `kicad-cli sch erc --severity-all --exit-code-violations hardware/verbot.kicad_sch` exits 0 and reports `0 Errors 0 Warnings`.
- No changes to any file under `src/` or `tests/`. This task adds no Python runtime dependencies.
- Pin assignments come from `src/verbot/config.py` and `docs/hardware.md`. Do not invent any.

## File Structure

| File | Responsibility |
|---|---|
| `hardware/verbot.kicad_pro` | KiCad project file |
| `hardware/verbot.kicad_sch` | The single A3 sheet (generated once, then hand-edited) |
| `hardware/verbot.kicad_sym` | Project symbol library: 5 module block symbols |
| `hardware/sym-lib-table` | Registers `verbot.kicad_sym` as nickname `verbot` |
| `hardware/tools/kicadgen.py` | Generator library: S-expression emitters + symbol geometry reader |
| `hardware/tools/generate_schematic.py` | One-shot script that lays out the sheet |
| `hardware/tools/README.md` | Explains that the generator is scaffolding and the `.kicad_sch` is authoritative |
| `hardware/verbot-schematic.pdf` | Exported, committed |
| `hardware/verbot-schematic.svg` | Exported, committed |
| `docs/hardware.md` | Modified: link to the PDF near the top |

---

### Task 1: Project scaffolding and the symbol-geometry reader

**Files:**
- Create: `hardware/verbot.kicad_pro`
- Create: `hardware/sym-lib-table`
- Create: `hardware/tools/kicadgen.py`
- Create: `hardware/tools/README.md`
- Test: `hardware/tools/selftest.py`

**Interfaces:**
- Consumes: nothing
- Produces: `kicadgen.extract_symbol(lib, name) -> str`, `kicadgen.pin_positions(lib, name) -> dict[str, Pin]`, `kicadgen.lib_symbol(lib, name) -> str`, `kicadgen.on_grid(v) -> float`, `kicadgen.assert_grid(*pts) -> None`, `kicadgen.uid() -> str`, and the dataclass `Pin(x: float, y: float, rot: int, name: str)`. Later tasks import all of these.

- [ ] **Step 1: Write the failing self-test**

`hardware/tools/selftest.py`:

```python
"""Self-checks for the schematic generator. Run: python3 hardware/tools/selftest.py"""

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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd hardware/tools && python3 selftest.py`
Expected: `ModuleNotFoundError: No module named 'kicadgen'`

- [ ] **Step 3: Write `hardware/tools/kicadgen.py`**

```python
"""S-expression emitters and KiCad symbol-geometry reader.

One-shot scaffolding for generating hardware/verbot.kicad_sch. Once that file
exists and has been opened in eeschema, IT is authoritative - see README.md.

Everything here targets KiCad 10 (schematic format 20260306). The 1.27mm grid
is enforced rather than assumed: off-grid endpoints are the most common cause
of ERC endpoint_off_grid warnings in hand-built files.
"""

import re
import uuid
from dataclasses import dataclass

SYMBOL_DIR = "/usr/share/kicad/symbols"
GRID = 1.27


@dataclass(frozen=True)
class Pin:
    x: float
    y: float
    rot: int
    name: str


def uid() -> str:
    return str(uuid.uuid4())


def on_grid(value: float) -> float:
    """Snap to the 1.27mm connection grid, rounded to 2dp for the file."""
    return round(round(value / GRID) * GRID, 2)


def assert_grid(*points: tuple[float, float]) -> None:
    """Raise if any point is off the connection grid.

    Called on every coordinate before it reaches the file. Catching it here
    gives a traceback pointing at the offending placement; catching it in ERC
    gives a millimetre position and a hunt.
    """
    for x, y in points:
        for axis, value in (("x", x), ("y", y)):
            if abs(value / GRID - round(value / GRID)) > 1e-6:
                raise ValueError(f"off-grid {axis}={value} (grid {GRID})")


def _library_path(lib: str) -> str:
    return f"{SYMBOL_DIR}/{lib}.kicad_sym"


def extract_symbol(lib: str, name: str) -> str:
    """Return the complete top-level (symbol "NAME" ...) block from a library.

    Paren-matching rather than regex: symbol bodies nest several levels deep
    and contain parentheses inside quoted strings.
    """
    text = open(_library_path(lib)).read()
    start = text.index(f'(symbol "{name}"')
    depth = 0
    in_string = False
    i = start
    while i < len(text):
        char = text[i]
        if in_string:
            if char == "\\":
                i += 2
                continue
            if char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    raise ValueError(f"unbalanced s-expression for {lib}:{name}")


def lib_symbol(lib: str, name: str) -> str:
    """The symbol block re-keyed to the "Lib:Name" form lib_symbols expects."""
    block = extract_symbol(lib, name)
    return block.replace(f'(symbol "{name}"', f'(symbol "{lib}:{name}"', 1)


_PIN_RE = re.compile(
    r'\(pin \w+ \w+\s*\n\s*\(at ([-\d.]+) ([-\d.]+) (\d+)\)'
    r'.*?\(name "([^"]*)".*?\(number "([^"]+)"',
    re.S,
)


def pin_positions(lib: str, name: str) -> dict[str, Pin]:
    """Map pin number -> Pin, in symbol-local coordinates.

    Symbol Y is inverted relative to schematic Y, so a symbol pin at +y appears
    at (origin_y - y) once placed. absolute_pin() does that conversion.
    """
    block = extract_symbol(lib, name)
    pins: dict[str, Pin] = {}
    for match in _PIN_RE.finditer(block):
        x, y, rot, pin_name, number = match.groups()
        pins[number] = Pin(float(x), float(y), int(rot), pin_name)
    return pins


def absolute_pin(origin: tuple[float, float], pin: Pin) -> tuple[float, float]:
    """Schematic coordinates of a pin on a symbol placed at origin, rotation 0."""
    return (round(origin[0] + pin.x, 2), round(origin[1] - pin.y, 2))
```

- [ ] **Step 4: Run the self-test to verify it passes**

Run: `cd hardware/tools && python3 selftest.py`
Expected: five `PASS` lines, exit 0.

If `test_pin_positions_reads_all_pi_header_pins` fails on the `Pin(5.08, 33.02, 270, "3V3")` comparison, print `pins["1"]` and correct the expected pin *name* only — the coordinates are verified correct. Stock library pin naming can vary between KiCad point releases.

- [ ] **Step 5: Write `hardware/verbot.kicad_pro`**

```json
{
  "board": {},
  "boards": [],
  "libraries": {
    "pinned_footprint_libs": [],
    "pinned_symbol_libs": []
  },
  "meta": {
    "filename": "verbot.kicad_pro",
    "version": 3
  },
  "net_settings": {
    "classes": [
      {
        "bus_width": 12,
        "clearance": 0.2,
        "name": "Default",
        "track_width": 0.2,
        "wire_width": 6
      }
    ]
  },
  "schematic": {
    "annotate_start_num": 0,
    "drawing": {
      "default_line_thickness": 6.0,
      "default_text_size": 50.0,
      "intersheets_ref_show": false
    },
    "legacy_lib_dir": "",
    "legacy_lib_list": []
  },
  "sheets": [],
  "text_variables": {}
}
```

- [ ] **Step 6: Write `hardware/sym-lib-table`**

```
(sym_lib_table
  (version 7)
  (lib (name "verbot")(type "KiCad")(uri "${KIPRJMOD}/verbot.kicad_sym")(options "")(descr "verbot-pi-ng module block symbols"))
)
```

- [ ] **Step 7: Write `hardware/tools/README.md`**

```markdown
# Schematic generator

One-shot scaffolding that produced `../verbot.kicad_sch`.

Writing a 40-pin header plus six module blocks as raw S-expressions by hand is
impractical — every coordinate must land on the 1.27mm grid, and the file must
embed a full copy of every symbol it uses. This generator reads the geometry
out of the KiCad libraries so those numbers are derived rather than typed.

**The generated `.kicad_sch` is authoritative.** Once it has been opened and
edited in eeschema, do not re-run the generator over it — you would discard
those edits. It is kept here because it documents how the sheet was built and
would save real work if the pin map ever changed wholesale.

```bash
cd hardware/tools
python3 selftest.py            # check the geometry reader
python3 generate_schematic.py  # regenerate (destructive)
```
```

- [ ] **Step 8: Commit**

```bash
git add hardware/
git commit -m "feat(hardware): KiCad project scaffolding and symbol geometry reader"
```

---

### Task 2: Project symbol library

**Files:**
- Create: `hardware/verbot.kicad_sym`
- Create: `hardware/tools/make_symbols.py`
- Modify: `hardware/tools/selftest.py` (add symbol library checks)

**Interfaces:**
- Consumes: `kicadgen.pin_positions`, `kicadgen.on_grid`
- Produces: library `verbot` containing symbols `DRV8833_Carrier`, `MAX98357A_Breakout`, `MCP23017_Breakout`, `OnOff_SHIM`, `Verbot_Gearbox`. Later tasks reference these as `verbot:NAME`.

Each symbol is a rectangle with pins on a 2.54mm pitch, pin names matching the board's silkscreen. All pins use `passive` electrical type except where noted, so ERC does not invent conflicts between two module blocks wired together.

- [ ] **Step 1: Write `hardware/tools/make_symbols.py`**

```python
"""Generate hardware/verbot.kicad_sym - block symbols for the bought modules.

Pin names match what is silkscreened on each board, so the schematic reads the
same as the part in your hand. Electrical types are deliberately conservative:
module pins are 'passive' unless the module actively drives them, which keeps
ERC focused on real wiring mistakes rather than type-conflict noise.
"""

from kicadgen import assert_grid

PITCH = 2.54


def rect_symbol(name, description, left_pins, right_pins, width=50.8):
    """Build a rectangular block symbol.

    left_pins/right_pins are lists of (number, name, etype). Pins are laid out
    top-down on 2.54mm pitch; the body grows to fit the taller side.
    """
    rows = max(len(left_pins), len(right_pins))
    height = (rows + 1) * PITCH
    top = height / 2
    half_w = width / 2
    assert_grid((half_w, top))

    out = [
        f'\t(symbol "{name}"',
        "\t\t(exclude_from_sim no)(in_bom yes)(on_board yes)",
        f'\t\t(property "Reference" "U" (at 0 {top + PITCH} 0)',
        "\t\t\t(effects (font (size 1.27 1.27))))",
        f'\t\t(property "Value" "{name}" (at 0 {-top - PITCH} 0)',
        "\t\t\t(effects (font (size 1.27 1.27))))",
        '\t\t(property "Footprint" "" (at 0 0 0)',
        "\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))",
        '\t\t(property "Datasheet" "" (at 0 0 0)',
        "\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))",
        f'\t\t(property "Description" "{description}" (at 0 0 0)',
        "\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))",
        f'\t\t(symbol "{name}_0_1"',
        f"\t\t\t(rectangle (start {-half_w} {top}) (end {half_w} {-top})",
        "\t\t\t\t(stroke (width 0.254) (type default))",
        "\t\t\t\t(fill (type background)))",
        "\t\t)",
        f'\t\t(symbol "{name}_1_1"',
    ]

    for side, pins in (("left", left_pins), ("right", right_pins)):
        for index, (number, pin_name, etype) in enumerate(pins):
            y = top - (index + 1) * PITCH
            if side == "left":
                x, rot = -half_w - PITCH, 0
            else:
                x, rot = half_w + PITCH, 180
            assert_grid((x, y))
            out += [
                f"\t\t\t(pin {etype} line (at {x} {y} {rot}) (length {PITCH})",
                f'\t\t\t\t(name "{pin_name}" (effects (font (size 1.27 1.27))))',
                f'\t\t\t\t(number "{number}" (effects (font (size 1.27 1.27))))',
                "\t\t\t)",
            ]
    out += ["\t\t)", "\t)"]
    return "\n".join(out)


def numbered(names, etype="passive", start=1):
    return [(str(i + start), n, etype) for i, n in enumerate(names)]


DRV8833 = rect_symbol(
    "DRV8833_Carrier",
    "DRV8833 dual H-bridge carrier board (channel A used)",
    numbered(["VCC", "GND", "EEP", "ULT", "IN1", "IN2"]),
    numbered(["OUT1", "OUT2", "IN3", "IN4", "OUT3", "OUT4"], start=7),
)

MAX98357A = rect_symbol(
    "MAX98357A_Breakout",
    "MAX98357A I2S DAC / class-D amplifier breakout",
    numbered(["VIN", "GND", "SD", "GAIN"]),
    numbered(["DIN", "BCLK", "LRC", "+", "-"], start=5),
)

MCP23017 = rect_symbol(
    "MCP23017_Breakout",
    "MCP23017 16-bit I2C GPIO expander breakout",
    numbered(
        ["VDD", "VSS", "SDA", "SCL", "RESET", "A0", "A1", "A2", "INTA", "INTB"]
    ),
    numbered([f"GPA{i}" for i in range(8)] + [f"GPB{i}" for i in range(8)], start=11),
    width=63.5,
)

ONOFF = rect_symbol(
    "OnOff_SHIM",
    "Pimoroni OnOff SHIM - soft power switch for Raspberry Pi",
    numbered(["USB_5V_IN", "GND"]),
    numbered(["5V_OUT", "BTN", "LED", "POWEROFF"], start=3),
    width=45.72,
)

GEARBOX = rect_symbol(
    "Verbot_Gearbox",
    "Tomy Verbot gearbox: 9-core interrogation switch harness and motor",
    numbered(
        [
            "WHITE_GND",
            "PURPLE",
            "RED",
            "YELLOW",
            "GREY",
            "BLUE",
            "BROWN",
            "ORANGE",
            "GREEN",
        ]
    ),
    numbered(["MOTOR_A", "MOTOR_B"], start=10),
    width=63.5,
)

LIBRARY = f"""(kicad_symbol_lib
	(version 20251024)
	(generator "verbot-make-symbols")
	(generator_version "10.0")
{DRV8833}
{MAX98357A}
{MCP23017}
{ONOFF}
{GEARBOX}
)
"""

if __name__ == "__main__":
    with open("../verbot.kicad_sym", "w") as handle:
        handle.write(LIBRARY)
    print("wrote hardware/verbot.kicad_sym")
```

- [ ] **Step 2: Generate the library**

Run: `cd hardware/tools && python3 make_symbols.py`
Expected: `wrote hardware/verbot.kicad_sym`

- [ ] **Step 3: Verify KiCad parses it**

Run: `kicad-cli sym upgrade --force hardware/verbot.kicad_sym`
Expected: no parse error. A message that the library is already at the current version is also acceptable.

If it reports a syntax error, the message names a line — the usual cause is a `(pin ...)` block outside the `_1_1` sub-symbol.

- [ ] **Step 4: Verify every symbol renders**

Run:
```bash
mkdir -p /tmp/symcheck && kicad-cli sym export svg -o /tmp/symcheck hardware/verbot.kicad_sym && ls /tmp/symcheck
```
Expected: five SVG files, one per symbol.

- [ ] **Step 5: Add library checks to the self-test**

Append to `hardware/tools/selftest.py`, above the `__main__` block:

```python
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
```

- [ ] **Step 6: Run the self-test**

Run: `cd hardware/tools && python3 selftest.py`
Expected: eight `PASS` lines, exit 0.

- [ ] **Step 7: Commit**

```bash
git add hardware/
git commit -m "feat(hardware): block symbols for DRV8833, MAX98357A, MCP23017, OnOff SHIM, gearbox"
```

---

### Task 3: Sheet generator and the power section

**Files:**
- Create: `hardware/tools/generate_schematic.py`
- Create: `hardware/verbot.kicad_sch` (generated)

**Interfaces:**
- Consumes: everything from `kicadgen`; the `verbot` library from Task 2.
- Produces: `generate_schematic.py` module-level helpers `sym()`, `wire()`, `label()`, `nc()`, `text_note()`, the `BODY` list, and the constant `PI_AT` (Pi header origin). Tasks 4–8 append to `BODY` and call these helpers with identical signatures.

This task builds the generator skeleton, places the Pi header, and wires the power tree. Nothing else is on the sheet yet.

- [ ] **Step 1: Write the generator skeleton — `hardware/tools/generate_schematic.py`**

Write this file complete, in one go. Steps 2 onward only append sections to it.

```python
"""Lay out hardware/verbot.kicad_sch.

One-shot scaffolding - see README.md. The generated file is authoritative once
eeschema has touched it.

Layout is a single A3 sheet: Pi header centred, module blocks around it.
Signals travel by net label off short pin stubs rather than long routed wires,
which is the readable convention for a 40-pin part and keeps this file
manageable.
"""

from kicadgen import (
    Pin,
    absolute_pin,
    assert_grid,
    lib_symbol,
    pin_positions,
    uid,
)

ROOT_UUID = uid()
PROJECT = "verbot"

# Every symbol placed on the sheet: (lib, name). Drives the lib_symbols cache.
USED_SYMBOLS: list[tuple[str, str]] = []

BODY: list[str] = []

STUB = 3.81  # 3 * 1.27 - stub length from a pin to its net label


def use(lib: str, name: str) -> str:
    if (lib, name) not in USED_SYMBOLS:
        USED_SYMBOLS.append((lib, name))
    return f"{lib}:{name}"


def sym(lib, name, ref, value, at, rot=0, hide_value=False):
    """Place a symbol instance and return its pin-position lookup."""
    lib_id = use(lib, name)
    pins = pin_positions(lib, name) if lib != "verbot" else _project_pins(name)
    x, y = at
    assert_grid(at)
    value_effects = "(hide yes)" if hide_value else ""
    pin_blocks = "\n".join(f'\t\t(pin "{n}" (uuid "{uid()}"))' for n in pins)
    BODY.append(
        f"""	(symbol
		(lib_id "{lib_id}")
		(at {x} {y} {rot})
		(unit 1)
		(exclude_from_sim no)
		(in_bom yes)
		(on_board yes)
		(dnp no)
		(uuid "{uid()}")
		(property "Reference" "{ref}" (at {x} {y - 2.54} 0)
			(effects (font (size 1.27 1.27)) (justify left)))
		(property "Value" "{value}" (at {x} {y + 2.54} 0)
			(effects (font (size 1.27 1.27)) (justify left) {value_effects}))
{pin_blocks}
		(instances
			(project "{PROJECT}"
				(path "/{ROOT_UUID}" (reference "{ref}") (unit 1))))
	)"""
    )
    return {number: absolute_pin(at, pin) for number, pin in pins.items()}


def _project_pins(name: str) -> dict[str, Pin]:
    """pin_positions() for the project library, which is not in SYMBOL_DIR."""
    import kicadgen

    original = kicadgen.SYMBOL_DIR
    kicadgen.SYMBOL_DIR = ".."
    try:
        return kicadgen.pin_positions("verbot", name)
    finally:
        kicadgen.SYMBOL_DIR = original


def wire(start, end):
    assert_grid(start, end)
    BODY.append(
        f"""	(wire (pts (xy {start[0]} {start[1]}) (xy {end[0]} {end[1]}))
		(stroke (width 0) (type default)) (uuid "{uid()}"))"""
    )


def label(text, at, rot=0):
    assert_grid(at)
    BODY.append(
        f"""	(label "{text}" (at {at[0]} {at[1]} {rot})
		(effects (font (size 1.27 1.27)) (justify left bottom)) (uuid "{uid()}"))"""
    )


def nc(at):
    """No-connect flag. Must sit exactly on a pin or ERC flags it as dangling."""
    assert_grid(at)
    BODY.append(f'	(no_connect (at {at[0]} {at[1]}) (uuid "{uid()}"))')


def text_note(body, at, size=1.27):
    BODY.append(
        f"""	(text "{body}" (at {at[0]} {at[1]} 0)
		(effects (font (size {size} {size})) (justify left top)) (uuid "{uid()}"))"""
    )


def stub_label(pin_xy, net, direction):
    """Draw a short stub off a pin and label it. direction: 'L' or 'R'."""
    dx = -STUB if direction == "L" else STUB
    end = (round(pin_xy[0] + dx, 2), pin_xy[1])
    wire(pin_xy, end)
    label(net, end)


def _lib_symbol_any(lib, name):
    """lib_symbol() that also resolves the project library at ../verbot.kicad_sym."""
    import kicadgen

    if lib != "verbot":
        return lib_symbol(lib, name)
    original = kicadgen.SYMBOL_DIR
    kicadgen.SYMBOL_DIR = ".."
    try:
        return kicadgen.lib_symbol(lib, name)
    finally:
        kicadgen.SYMBOL_DIR = original


# --------------------------------------------------------------------------
# Power tree
# --------------------------------------------------------------------------

PI_AT = (215.9, 148.59)

pi = sym("Connector", "Raspberry_Pi_2_3", "J1", "Raspberry Pi Zero 2 W", PI_AT)
shim = sym("verbot", "OnOff_SHIM", "M1", "Pimoroni OnOff SHIM", (69.85, 63.5))
usb = sym("Connector", "USB_B_Micro", "J2", "5V USB power bank", (19.05, 63.5))

# USB bank -> SHIM. VBUS is pin 1 and shield/GND pin 5 on USB_B_Micro; confirm
# with pin_positions("Connector", "USB_B_Micro") and adjust if the stock symbol
# numbers them differently.
stub_label(usb["1"], "VBUS_IN", "R")
stub_label(usb["5"], "GND", "R")

# This is a power-only connection to a USB power bank; the data/ID/shield
# pins are intentionally unused, and J2 is not touched by any later task.
nc(usb["2"])  # D-
nc(usb["3"])  # D+
nc(usb["4"])  # ID
nc(usb["SH"])  # Shield

# Pi power pins. Pin numbers are physical header positions.
PI_5V = ["2", "4"]
PI_3V3 = ["1", "17"]
PI_GND = ["6", "9", "14", "20", "25", "30", "34", "39"]

for number in PI_5V:
    stub_label(pi[number], "+5V", "L")
for number in PI_3V3:
    stub_label(pi[number], "+3V3", "L")
for number in PI_GND:
    stub_label(pi[number], "GND", "R")

# OnOff SHIM: USB in, 5V out to the Pi rail, three GPIO lines.
stub_label(shim["1"], "VBUS_IN", "L")   # USB_5V_IN
stub_label(shim["2"], "GND", "L")
stub_label(shim["3"], "+5V", "R")       # 5V_OUT
stub_label(shim["4"], "SHIM_BTN", "R")  # BTN  - BCM17, Pi pin 11
stub_label(shim["5"], "SHIM_LED", "R")  # LED  - BCM27, Pi pin 13
stub_label(shim["6"], "SHIM_POWEROFF", "R")  # POWEROFF - BCM4, Pi pin 7

stub_label(pi["11"], "SHIM_BTN", "L")
stub_label(pi["13"], "SHIM_LED", "L")
stub_label(pi["7"], "SHIM_POWEROFF", "L")

# Power flags. Without one per externally-driven net, ERC errors with
# power_pin_not_driven. GND does not need one: the stock USB_B_Micro symbol
# types its pin 5 as a power output, so GND is already driven and adding a
# PWR_FLAG there trips a pin_to_pin "two power outputs" conflict instead.
for index, (net, at) in enumerate(
    [("+5V", (44.45, 105.41)), ("+3V3", (69.85, 105.41))]
):
    flag = sym("power", "PWR_FLAG", f"#FLG0{index + 1}", "PWR_FLAG", at, hide_value=True)
    label(net, flag["1"])

text_note(
    "POWER: 5V USB bank -> OnOff SHIM -> Pi 5V rail and DRV8833 VCC.\\n"
    "Motor current therefore passes through the SHIM load switch (~2A).\\n"
    "Cap VERBOT_ACTION_SPEED near +/-60 and rely on C1 for transients.",
    (25.4, 120.65),
)

# --------------------------------------------------------------------------
# Emit
# --------------------------------------------------------------------------

lib_cache = "\n".join(_lib_symbol_any(lib, name) for lib, name in USED_SYMBOLS)

DOC = f"""(kicad_sch
	(version 20260306)
	(generator "verbot-generate-schematic")
	(generator_version "10.0")
	(uuid "{ROOT_UUID}")
	(paper "A3")
	(title_block
		(title "verbot-pi-ng wiring")
		(date "2026-08-15")
		(rev "1")
		(comment 1 "Wiring documentation - not a PCB design")
		(comment 2 "Pin map source of truth: src/verbot/config.py")
	)
	(lib_symbols
{lib_cache}
	)
{chr(10).join(BODY)}
	(sheet_instances
		(path "/" (page "1"))
	)
	(embedded_fonts no)
)
"""

if __name__ == "__main__":
    with open("../verbot.kicad_sch", "w") as handle:
        handle.write(DOC)
    print(f"wrote hardware/verbot.kicad_sch ({len(BODY)} objects)")
```

- [ ] **Step 2: Generate and run ERC**

Run:
```bash
cd hardware/tools && python3 generate_schematic.py && cd ../.. && \
kicad-cli sch erc --severity-all --exit-code-violations hardware/verbot.kicad_sch; \
cat hardware/verbot-erc.rpt
```
Expected: `0 Errors 0 Warnings`, exit 0.

Common failures and their fixes:
- `endpoint_off_grid` — a coordinate is not a multiple of 1.27. `assert_grid` should have caught it; check any literal you typed.
- `power_pin_not_driven` — a `PWR_FLAG` label does not exactly match the net name it should drive.
- `pin_not_connected` — a Pi header pin has neither a stub nor a `no_connect`. Unused GPIO pins get `no_connect` in Task 8, so expect these until then; if they appear now, confirm they are only on pins Tasks 4–7 will claim, and proceed.

- [ ] **Step 3: Commit**

```bash
git add hardware/
git commit -m "feat(hardware): sheet generator and power tree"
```

---

### Task 4: Motor section

**Files:**
- Modify: `hardware/tools/generate_schematic.py` (insert before the `# Emit` banner)

**Interfaces:**
- Consumes: `sym`, `stub_label`, `wire`, `label`, `nc`, `text_note`, `pi` from Task 3.
- Produces: nets `MOTOR_IN1`, `MOTOR_IN2`, `MOTOR_nSLEEP`, `MOTOR_nFAULT`, `MOTOR_A`, `MOTOR_B`.

- [ ] **Step 1: Insert the motor section**

```python
# --------------------------------------------------------------------------
# Motor: DRV8833 channel A
# --------------------------------------------------------------------------

drv = sym("verbot", "DRV8833_Carrier", "M2", "DRV8833 carrier", (330.2, 63.5))
motor = sym("Device", "Motor_DC", "M3", "Verbot 3V motor", (403.86, 76.2))
c1 = sym("Device", "C_Polarized", "C1", "470uF", (292.1, 76.2))

# Pi -> DRV8833. BCM numbers from src/verbot/config.py.
MOTOR_NETS = [
    ("32", "5", "MOTOR_IN1"),      # BCM12 PWM0  -> IN1
    ("33", "6", "MOTOR_IN2"),      # BCM13 PWM1  -> IN2
    ("31", "3", "MOTOR_nSLEEP"),   # BCM6        -> EEP
    ("36", "4", "MOTOR_nFAULT"),   # BCM16       <- ULT
]
for pi_pin, drv_pin, net in MOTOR_NETS:
    stub_label(pi[pi_pin], net, "R")
    stub_label(drv[drv_pin], net, "L")

stub_label(drv["1"], "+5V", "L")   # VCC
stub_label(drv["2"], "GND", "L")   # GND

# Bulk capacitance across the motor rail, at the carrier.
label("+5V", c1["1"])
label("GND", c1["2"])

# Channel A out to the motor.
stub_label(drv["7"], "MOTOR_A", "R")   # OUT1
stub_label(drv["8"], "MOTOR_B", "R")   # OUT2
stub_label(motor["1"], "MOTOR_A", "L")
stub_label(motor["2"], "MOTOR_B", "L")

# Channel B is unused.
for drv_pin in ("9", "10", "11", "12"):   # IN3, IN4, OUT3, OUT4
    nc(drv[drv_pin])

text_note(
    "MOTOR: only channel A is used.\\n"
    "EEP may be tied to VCC by the carrier's J1 solder jumper - check with a\\n"
    "meter. If bridged, set VERBOT_MOTOR_SLEEP_PIN=null and BCM6 stays free.\\n"
    "If the motor runs backwards, swap OUT1/OUT2 rather than the code's sign\\n"
    "convention: interrogation must be the positive direction.\\n"
    "VCC is the motor rail, NOT a logic rail - the 3V motor sees whatever it\\n"
    "is fed. Never feed it from the Pi's 3V3 pin.",
    (292.1, 116.84),
)
```

- [ ] **Step 2: Regenerate and run ERC**

Run:
```bash
cd hardware/tools && python3 generate_schematic.py && cd ../.. && \
kicad-cli sch erc --severity-all --exit-code-violations hardware/verbot.kicad_sch; \
cat hardware/verbot-erc.rpt
```
Expected: no new violations beyond unclaimed Pi GPIO pins.

If `Motor_DC` pin numbers are not `"1"` and `"2"`, run
`python3 -c "import sys; sys.path.insert(0,'hardware/tools'); from kicadgen import pin_positions; print(pin_positions('Device','Motor_DC'))"`
and use the numbers it reports. Same technique for `C_Polarized`.

- [ ] **Step 3: Commit**

```bash
git add hardware/
git commit -m "feat(hardware): DRV8833 motor section"
```

---

### Task 5: Interrogation switch bank

**Files:**
- Modify: `hardware/tools/generate_schematic.py` (insert before the `# Emit` banner)

**Interfaces:**
- Consumes: `sym`, `stub_label`, `text_note`, `pi`.
- Produces: nets `SW_STOP`, `SW_ROTATE_RIGHT`, `SW_ROTATE_LEFT`, `SW_FORWARDS`, `SW_REVERSE`, `SW_PUT_DOWN`, `SW_PICK_UP`, `SW_TALK`.

- [ ] **Step 1: Insert the switch bank section**

```python
# --------------------------------------------------------------------------
# Interrogation switch bank - 9-core ribbon from the gearbox
# --------------------------------------------------------------------------

gearbox = sym("verbot", "Verbot_Gearbox", "M4", "Verbot gearbox harness", (330.2, 190.5))

# (gearbox pin, ribbon colour, Pi physical pin, net, BCM, interrogation order)
# Pin map is DEFAULT_SWITCH_PINS in src/verbot/config.py.
SWITCHES = [
    ("2", "PURPLE", "15", "SW_STOP", 22, 1),
    ("3", "RED", "37", "SW_ROTATE_RIGHT", 26, 2),
    ("4", "YELLOW", "19", "SW_ROTATE_LEFT", 10, 3),
    ("5", "GREY", "21", "SW_FORWARDS", 9, 4),
    ("6", "BLUE", "22", "SW_REVERSE", 25, 5),
    ("7", "BROWN", "23", "SW_PUT_DOWN", 11, 6),
    ("8", "ORANGE", "24", "SW_PICK_UP", 8, 7),
    ("9", "GREEN", "26", "SW_TALK", 7, 8),
]

for gearbox_pin, _colour, pi_pin, net, _bcm, _order in SWITCHES:
    stub_label(gearbox[gearbox_pin], net, "L")
    stub_label(pi[pi_pin], net, "R")

stub_label(gearbox["1"], "GND", "L")   # WHITE - common return
stub_label(gearbox["10"], "MOTOR_A", "R")
stub_label(gearbox["11"], "MOTOR_B", "R")

text_note(
    "INTERROGATION SWITCHES - all inputs, internal pull-ups, ACTIVE LOW.\\n"
    "White is the common ground return for all eight.\\n"
    "\\n"
    "  colour   order  action         BCM  Pi pin\\n"
    "  purple     1    stop            22    15\\n"
    "  red        2    rotate right    26    37\\n"
    "  yellow     3    rotate left     10    19\\n"
    "  grey       4    forwards         9    21\\n"
    "  blue       5    reverse         25    22\\n"
    "  brown      6    put down        11    23\\n"
    "  orange     7    pick up          8    24\\n"
    "  green      8    talk             7    26\\n"
    "\\n"
    "ARM LIMIT SWITCHES are in series inside the gearbox on BROWN and ORANGE.\\n"
    "When an arm reaches its travel limit that circuit OPENS - the controller\\n"
    "sees the switch release mid-action and stops. Without it the mechanism\\n"
    "strains against its stop.",
    (241.3, 215.9),
)
```

- [ ] **Step 2: Regenerate and run ERC**

Run:
```bash
cd hardware/tools && python3 generate_schematic.py && cd ../.. && \
kicad-cli sch erc --severity-all --exit-code-violations hardware/verbot.kicad_sch; \
cat hardware/verbot-erc.rpt
```
Expected: no new violations.

- [ ] **Step 3: Verify the pin map against the code**

Run:
```bash
python3 - <<'EOF'
import re
src = open("src/verbot/config.py").read()
block = re.search(r"DEFAULT_SWITCH_PINS.*?\{(.*?)\}", src, re.S).group(1)
code = dict(re.findall(r"Action\.(\w+):\s*(\d+)", block))
sch = {"STOP": "22", "ROTATE_RIGHT": "26", "ROTATE_LEFT": "10", "FORWARDS": "9",
       "REVERSE": "25", "PUT_DOWN": "11", "PICK_UP": "8", "TALK": "7"}
assert code == sch, f"MISMATCH\ncode={code}\nsch ={sch}"
print("switch pin map matches src/verbot/config.py")
EOF
```
Expected: `switch pin map matches src/verbot/config.py`

- [ ] **Step 4: Commit**

```bash
git add hardware/
git commit -m "feat(hardware): interrogation switch bank and limit switches"
```

---

### Task 6: Audio section

**Files:**
- Modify: `hardware/tools/generate_schematic.py` (insert before the `# Emit` banner)

**Interfaces:**
- Consumes: `sym`, `stub_label`, `nc`, `text_note`, `pi`.
- Produces: nets `I2S_BCLK`, `I2S_LRCLK`, `I2S_DIN`, `SPK_P`, `SPK_N`.

- [ ] **Step 1: Insert the audio section**

```python
# --------------------------------------------------------------------------
# Audio: MAX98357A I2S DAC / amplifier
# --------------------------------------------------------------------------

dac = sym("verbot", "MAX98357A_Breakout", "M5", "MAX98357A I2S DAC", (144.78, 215.9))
spk = sym("Device", "Speaker", "LS1", "4-8 ohm passive", (63.5, 228.6))

stub_label(dac["1"], "+5V", "L")   # VIN
stub_label(dac["2"], "GND", "L")   # GND

# Pi I2S -> DAC.
I2S_NETS = [
    ("12", "6", "I2S_BCLK"),    # BCM18 -> BCLK
    ("35", "7", "I2S_LRCLK"),   # BCM19 -> LRC
    ("40", "5", "I2S_DIN"),     # BCM21 -> DIN
]
for pi_pin, dac_pin, net in I2S_NETS:
    stub_label(pi[pi_pin], net, "L")
    stub_label(dac[dac_pin], net, "R")

# SD and GAIN ride the breakout's own pull-ups.
nc(dac["3"])   # SD
nc(dac["4"])   # GAIN

stub_label(dac["8"], "SPK_P", "R")
stub_label(dac["9"], "SPK_N", "R")
stub_label(spk["1"], "SPK_P", "L")
stub_label(spk["2"], "SPK_N", "L")

text_note(
    "AUDIO: MAX98357A on BCM18 (BCLK), 19 (LRCLK), 21 (DIN).\\n"
    "SD_MODE is left floating on the breakout's own pull-up. That is what the\\n"
    "no-sdmode flag in config/config.txt.example selects - without it the\\n"
    "overlay claims BCM4, which the OnOff SHIM needs for shutdown.\\n"
    "GAIN floating = 9dB default. Speaker must be PASSIVE, 4-8 ohm.",
    (63.5, 254.0),
)
```

- [ ] **Step 2: Regenerate and run ERC**

Run:
```bash
cd hardware/tools && python3 generate_schematic.py && cd ../.. && \
kicad-cli sch erc --severity-all --exit-code-violations hardware/verbot.kicad_sch; \
cat hardware/verbot-erc.rpt
```
Expected: no new violations.

- [ ] **Step 3: Commit**

```bash
git add hardware/
git commit -m "feat(hardware): MAX98357A audio section"
```

---

### Task 7: Front panel — MCP23017, keypad, status LED

**Files:**
- Modify: `hardware/tools/generate_schematic.py` (insert before the `# Emit` banner)

**Interfaces:**
- Consumes: `sym`, `stub_label`, `wire`, `label`, `nc`, `text_note`, `pi`.
- Produces: nets `I2C_SDA`, `I2C_SCL`, `BTN_<ACTION>` ×8, `LED_A`.

- [ ] **Step 1: Insert the front-panel section**

```python
# --------------------------------------------------------------------------
# Front panel: MCP23017 expander, 8 keypad buttons, status LED
# --------------------------------------------------------------------------

mcp = sym("verbot", "MCP23017_Breakout", "M6", "MCP23017 @ 0x20", (76.2, 165.1))

stub_label(mcp["1"], "+3V3", "L")   # VDD
stub_label(mcp["2"], "GND", "L")    # VSS
stub_label(mcp["3"], "I2C_SDA", "L")
stub_label(mcp["4"], "I2C_SCL", "L")
stub_label(mcp["5"], "+3V3", "L")   # RESET held high

# A0/A1/A2 to GND -> address 0x20, matching Settings.mcp23017_address.
for mcp_pin in ("6", "7", "8"):
    stub_label(mcp[mcp_pin], "GND", "L")

# Interrupts unused: Mcp23017Keypad polls at 50 Hz.
nc(mcp["9"])    # INTA
nc(mcp["10"])   # INTB

stub_label(pi["3"], "I2C_SDA", "L")   # BCM2
stub_label(pi["5"], "I2C_SCL", "L")   # BCM3

# GPA0-7 -> the eight original red buttons, common side to GND.
# Order is BUTTON_ORDER in src/verbot/hardware/mcp23017.py.
BUTTON_ORDER = [
    "STOP",
    "FORWARDS",
    "REVERSE",
    "ROTATE_LEFT",
    "ROTATE_RIGHT",
    "PICK_UP",
    "PUT_DOWN",
    "TALK",
]
for index, action in enumerate(BUTTON_ORDER):
    mcp_pin = str(11 + index)          # GPA0 is pin 11 in the block symbol
    net = f"BTN_{action}"
    stub_label(mcp[mcp_pin], net, "R")
    button = sym(
        "Switch", "SW_Push", f"SW{index + 1}", action.lower(),
        (12.7, round(139.7 + index * 10.16, 2)),
    )
    stub_label(button["1"], net, "L")
    stub_label(button["2"], "GND", "R")

# GPB0 -> series resistor -> status LED -> GND.
led_r = sym("Device", "R", "R1", "330", (152.4, 165.1))
led = sym("Device", "LED", "D1", "red status", (152.4, 180.34))
stub_label(mcp["19"], "LED_DRIVE", "R")   # GPB0
label("LED_DRIVE", led_r["1"])
label("LED_A", led_r["2"])
label("LED_A", led["1"])
label("GND", led["2"])

# GPB1-7 are configured as inputs by the driver and left floating.
for index in range(1, 8):
    nc(mcp[str(19 + index)])

text_note(
    "FRONT PANEL - VERIFY BEFORE WIRING.\\n"
    "This assumes the eight buttons are INDEPENDENT switches to a common rail.\\n"
    "That has NOT been confirmed on the real panel. The original PCB also\\n"
    "carried the power switching, so bypass it and solder to the switch\\n"
    "contacts directly. Check with a meter first.\\n"
    "\\n"
    "GPA0-7 are inputs with MCP23017 pull-ups enabled: ACTIVE LOW.\\n"
    "Button order above is BUTTON_ORDER in hardware/mcp23017.py and is a\\n"
    "GUESS - reorder it at bring-up if a button triggers the wrong action.\\n"
    "R1 330R gives ~5mA from 3V3 through a red LED; MCP23017 sources 25mA.\\n"
    "GPB1-7 float (driver sets them as inputs) - harmless.",
    (12.7, 254.0),
)
```

- [ ] **Step 2: Regenerate and run ERC**

Run:
```bash
cd hardware/tools && python3 generate_schematic.py && cd ../.. && \
kicad-cli sch erc --severity-all --exit-code-violations hardware/verbot.kicad_sch; \
cat hardware/verbot-erc.rpt
```
Expected: no new violations.

- [ ] **Step 3: Verify button order against the code**

Run:
```bash
python3 - <<'EOF'
import re
src = open("src/verbot/hardware/mcp23017.py").read()
block = re.search(r"BUTTON_ORDER.*?\((.*?)\)", src, re.S).group(1)
code = re.findall(r"Action\.(\w+)", block)
sch = ["STOP", "FORWARDS", "REVERSE", "ROTATE_LEFT",
       "ROTATE_RIGHT", "PICK_UP", "PUT_DOWN", "TALK"]
assert code == sch, f"MISMATCH\ncode={code}\nsch ={sch}"
print("button order matches src/verbot/hardware/mcp23017.py")
EOF
```
Expected: `button order matches src/verbot/hardware/mcp23017.py`

- [ ] **Step 4: Commit**

```bash
git add hardware/
git commit -m "feat(hardware): MCP23017 front panel, keypad and status LED"
```

---

### Task 8: Unused Pi pins, final ERC clean

**Files:**
- Modify: `hardware/tools/generate_schematic.py` (insert before the `# Emit` banner)

**Interfaces:**
- Consumes: `nc`, `pi`, `text_note`.
- Produces: an ERC-clean sheet.

- [ ] **Step 1: Insert the unused-pin section**

```python
# --------------------------------------------------------------------------
# Unused header pins
# --------------------------------------------------------------------------

# Every Pi pin claimed above, so the rest can be no-connected without guessing.
CLAIMED = set(
    PI_5V + PI_3V3 + PI_GND
    + ["11", "13", "7"]                                  # OnOff SHIM
    + [pi_pin for pi_pin, _, _ in MOTOR_NETS]            # DRV8833
    + [pi_pin for _, _, pi_pin, _, _, _ in SWITCHES]     # switch bank
    + [pi_pin for pi_pin, _, _ in I2S_NETS]              # I2S DAC
    + ["3", "5"]                                         # I2C
)

for number in pi:
    if number not in CLAIMED:
        nc(pi[number])

text_note(
    "FREE GPIO after this build: BCM 5, 14, 15, 20, 23, 24.\\n"
    "Unused header pins carry no-connect flags so ERC stays meaningful.",
    (215.9, 254.0),
)
```

- [ ] **Step 2: Regenerate and run ERC — this is the gate**

Run:
```bash
cd hardware/tools && python3 generate_schematic.py && cd ../.. && \
kicad-cli sch erc --severity-all --exit-code-violations hardware/verbot.kicad_sch; \
echo "exit=$?"; cat hardware/verbot-erc.rpt
```
Expected: `** ERC messages: 0  Errors 0  Warnings 0` and `exit=0`.

Do not proceed until this is exactly zero. If violations remain:
- `endpoint_off_grid` — an off-grid literal slipped past `assert_grid`; grep the file for coordinates and check each against 1.27.
- `no_connect_dangling` — a `nc()` is not exactly on a pin. Print the pin dict for that symbol and compare.
- `pin_not_connected` — a module pin has neither a stub nor a no-connect.
- `power_pin_not_driven` — a net that feeds a power-input pin has no `PWR_FLAG`.
- `label_dangling` — a net label appears only once. Every net name must appear at both ends.

- [ ] **Step 3: Commit**

```bash
git add hardware/
git commit -m "feat(hardware): no-connect unused header pins, ERC clean"
```

---

### Task 9: Export, document, publish

**Files:**
- Create: `hardware/verbot-schematic.pdf`
- Create: `hardware/verbot-schematic.svg`
- Modify: `docs/hardware.md`
- Modify: `README.md`

- [ ] **Step 1: Export PDF and SVG**

Run:
```bash
kicad-cli sch export pdf -o hardware/verbot-schematic.pdf hardware/verbot.kicad_sch && \
kicad-cli sch export svg --exclude-drawing-sheet -o /tmp/verbot-svg hardware/verbot.kicad_sch && \
cp /tmp/verbot-svg/verbot.svg hardware/verbot-schematic.svg && \
ls -la hardware/verbot-schematic.*
```
Expected: both files exist and are non-empty.

If `--exclude-drawing-sheet` is rejected, drop the flag — it only omits the border.

- [ ] **Step 2: Read the PDF and check it visually**

Open `hardware/verbot-schematic.pdf` and confirm:
- No block overlaps another block or a text note
- Every net label is legible and not colliding with a wire
- The title block reads "verbot-pi-ng wiring"
- All six notes are on the sheet and inside the border

If blocks overlap, adjust the `at=` coordinates in `generate_schematic.py` (keeping every value a multiple of 1.27), regenerate, re-run ERC, and re-export.

- [ ] **Step 3: Link the schematic from `docs/hardware.md`**

Insert immediately after the attribution blockquote at the top:

```markdown
> 📐 **Schematic:** [`hardware/verbot-schematic.pdf`](../hardware/verbot-schematic.pdf)
> — the full wiring on one sheet. Source: [`hardware/verbot.kicad_sch`](../hardware/verbot.kicad_sch)
> (KiCad 10). The GPIO table below and `src/verbot/config.py` remain the pin-map
> source of truth; the schematic reproduces them.
```

- [ ] **Step 4: Link it from `README.md`**

In the `## Hardware` section, change:

```markdown
Wiring, pin assignments and the reverse-engineered mechanical details are in
[`docs/hardware.md`](docs/hardware.md).
```

to:

```markdown
Wiring, pin assignments and the reverse-engineered mechanical details are in
[`docs/hardware.md`](docs/hardware.md), and the full schematic is at
[`hardware/verbot-schematic.pdf`](hardware/verbot-schematic.pdf).
```

- [ ] **Step 5: Final verification**

Run:
```bash
cd hardware/tools && python3 selftest.py && cd ../.. && \
kicad-cli sch erc --severity-all --exit-code-violations hardware/verbot.kicad_sch && \
uv run pytest -q && uv run ruff check
```
Expected: self-test passes, ERC exits 0, the existing Python suite still passes, ruff clean.

The Python suite must be untouched by this work — if it fails, something outside `hardware/` was modified.

- [ ] **Step 6: Commit and push**

```bash
git add hardware/ docs/hardware.md README.md
git commit -m "feat(hardware): KiCad wiring schematic with PDF/SVG exports"
git push origin main
```

---

## Notes for the implementer

**On the generator.** Tasks 3–8 build one Python file incrementally, each appending a section before the `# Emit` banner. That ordering matters: `BODY` and `USED_SYMBOLS` are populated at import time, and the `DOC` f-string reads them.

**On coordinates.** Every `at=` in this plan is a multiple of 1.27 and was chosen to keep blocks apart on a 420×297mm sheet. They are starting positions, not sacred — if the PDF looks cramped, move them. Just keep them on grid.

**On what this is not.** No footprints, no netlist for fabrication, no PCB. If a task tempts you toward assigning footprints, it is out of scope.
