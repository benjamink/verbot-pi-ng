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
\t(version 20251024)
\t(generator "verbot-make-symbols")
\t(generator_version "10.0")
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
