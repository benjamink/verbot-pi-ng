"""S-expression emitters and KiCad symbol-geometry reader.

One-shot scaffolding for generating hardware/verbot.kicad_sch. Once that file
exists and has been opened in eeschema, IT is authoritative - see README.md.

Everything here targets KiCad 10 (schematic format 20260306). The 1.27mm grid
is enforced rather than assumed: off-grid endpoints are the most common cause
of ERC endpoint_off_grid warnings in hand-built files.
"""

import math
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


@dataclass(frozen=True)
class PropertyPlacement:
    """Where a library says its Reference/Value text belongs, in symbol coords.

    dx/dy are offsets from the symbol origin; symbol Y is inverted relative to
    schematic Y, exactly as for pins (see absolute_pin).
    """

    dx: float
    dy: float
    rot: int
    justify: str
    hide: bool


def uid() -> str:
    return str(uuid.uuid4())


def on_grid(value: float) -> float:
    """Snap to the 1.27mm connection grid, rounded to 2dp for the file."""
    return round(round(value / GRID) * GRID, 2)


def grid_out(value: float) -> float:
    """Snap outward to the 1.27mm grid - away from zero, never towards it.

    Used for library text offsets, several of which are deliberately off-grid
    (Device:R's reference at 2.032mm). Rounding to the nearest grid step would
    sometimes pull two adjacent labels onto the same line; rounding outward
    only ever opens the gap up.
    """
    steps = math.ceil(abs(value) / GRID - 1e-9)
    return round(math.copysign(steps * GRID, value), 2)


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


def balanced(text: str, start: int) -> str:
    """Return the s-expression beginning at text[start], parens balanced.

    Paren-matching rather than regex: symbol bodies nest several levels deep
    and contain parentheses inside quoted strings.
    """
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
    raise ValueError(f"unbalanced s-expression at offset {start}")


def extract_symbol(lib: str, name: str) -> str:
    """Return the complete top-level (symbol "NAME" ...) block from a library."""
    text = open(_library_path(lib)).read()
    return balanced(text, text.index(f'(symbol "{name}"'))


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


_AT_RE = re.compile(r"\(at ([-\d.]+) ([-\d.]+) (\d+)\)")
_JUSTIFY_RE = re.compile(r"\(justify ([a-z ]+)\)")


def property_placements(lib: str, name: str) -> dict[str, PropertyPlacement]:
    """Map property name -> where the library itself puts that text.

    The stock and project libraries already place Reference above and Value
    below each symbol, clear of the body and of the pin names. Deriving the
    schematic instance's positions from those beats inventing a fixed offset
    from the origin, which lands inside the body of any symbol taller than a
    few millimetres.
    """
    block = extract_symbol(lib, name)
    placements: dict[str, PropertyPlacement] = {}
    for match in re.finditer(r'\(property "([^"]+)" "', block):
        body = balanced(block, match.start())
        at = _AT_RE.search(body)
        if at is None:
            continue
        justify = _JUSTIFY_RE.search(body)
        placements[match.group(1)] = PropertyPlacement(
            dx=grid_out(float(at.group(1))),
            dy=grid_out(float(at.group(2))),
            rot=int(at.group(3)),
            justify=justify.group(1).strip() if justify else "",
            hide="(hide yes)" in body,
        )
    return placements


def absolute_pin(origin: tuple[float, float], pin: Pin) -> tuple[float, float]:
    """Schematic coordinates of a pin on a symbol placed at origin, rotation 0."""
    return (round(origin[0] + pin.x, 2), round(origin[1] - pin.y, 2))
