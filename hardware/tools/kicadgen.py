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
