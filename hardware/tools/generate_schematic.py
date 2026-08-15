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
stub_label(shim["1"], "VBUS_IN", "L")  # USB_5V_IN
stub_label(shim["2"], "GND", "L")
stub_label(shim["3"], "+5V", "R")  # 5V_OUT
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
# Motor: DRV8833 channel A
# --------------------------------------------------------------------------

drv = sym("verbot", "DRV8833_Carrier", "M2", "DRV8833 carrier", (330.2, 63.5))
motor = sym("Motor", "Motor_DC", "M3", "Verbot 3V motor", (403.86, 76.2))
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
# pin_positions("Device", "LED") -> pin "1" is K (cathode), pin "2" is A
# (anode). Anode gets the driven net; cathode goes to GND.
label("LED_A", led["2"])
label("GND", led["1"])

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
