# KiCad wiring schematic — design

**Date:** 2026-08-15
**Status:** approved, ready for implementation

## Goal

Produce a KiCad schematic documenting how every part of the robot wires to the
Raspberry Pi Zero 2 W, readable enough to build and debug from at the bench.

This is **wiring documentation, not a PCB design**. No footprints, no netlist
for fabrication, no board layout. Modules are drawn as block symbols with the
pin names silkscreened on the real boards, because the modules are bought
boards being hand-wired — not chips being designed in.

`src/verbot/config.py` and `docs/hardware.md` are the source of truth for pin
assignments. The schematic reproduces them; it does not invent any.

## Deliverables

```
hardware/
  verbot.kicad_pro          project file
  verbot.kicad_sch          single A3 sheet
  verbot.kicad_sym          project symbol library (module blocks)
  sym-lib-table             registers verbot.kicad_sym as "verbot"
  verbot-schematic.pdf      exported, committed
  verbot-schematic.svg      exported, committed
```

`hardware/` is a new top-level directory. `docs/` is prose; this is neither
prose nor Python. The PDF and SVG are committed so the wiring is readable from
GitHub without KiCad installed — the realistic case is standing at the bench
with a phone.

`docs/hardware.md` gains a link to the PDF near the top. Its GPIO table stays
where it is; that table is what gets grepped.

Target: KiCad 10.0.5, symbol library format version 20251024.

## Symbol library

Five block symbols in `verbot.kicad_sym`. Each is a labelled rectangle whose
pins carry the names printed on the actual board, so the schematic reads the
same as the part in your hand.

| Symbol | Pins |
|---|---|
| `DRV8833_Carrier` | `VCC` `GND` `IN1` `IN2` `IN3` `IN4` `OUT1` `OUT2` `OUT3` `OUT4` `EEP` `ULT` |
| `MAX98357A_Breakout` | `VIN` `GND` `SD` `GAIN` `DIN` `BCLK` `LRC` `+` `-` |
| `MCP23017_Breakout` | `VDD` `VSS` `SDA` `SCL` `RESET` `A0` `A1` `A2` `INTA` `INTB` `GPA0`–`GPA7` `GPB0`–`GPB7` |
| `OnOff_SHIM` | `USB_5V_IN` `5V_OUT` `GND` `BTN` `LED` `POWEROFF` |
| `Verbot_Gearbox` | `WHITE_GND` `PURPLE` `RED` `YELLOW` `GREY` `BLUE` `BROWN` `ORANGE` `GREEN` `MOTOR_A` `MOTOR_B` |

`Verbot_Gearbox` names its pins by ribbon-cable wire colour. Those colours are
what you are physically holding when you strip that 9-core cable, and mapping
colour to BCM number is the single step where an error is most expensive.

Stock symbols for everything else:

- `Connector:Raspberry_Pi_2_3` — the 40-pin header. Electrically identical on a
  Zero 2 W; the symbol name refers to the header pinout, not the board model.
- `Device:Speaker`, `Motor:Motor_DC`, `Device:LED`, `Device:R`,
  `Device:C_Polarized`, `Switch:SW_Push`
- `Connector:USB_B_Micro` for the power bank's cable into the OnOff SHIM,
  labelled as the 5V bank rather than drawn as a battery

## Sheet layout

Single A3 sheet. Pi header centred, six blocks around it. Signals travel by net
label off short pin stubs rather than long routed wires — the readable
convention for a 40-pin part, and it keeps the S-expressions tractable.

```
┌─A3──────────────────────────────────────┐
│ [USB bank]   ┌───────┐      [DRV8833]   │
│ [OnOff SHIM] │  Pi   │      [MOTOR]     │
│              │ Zero  │                  │
│ [MCP23017]   │ 2 W   │   [GEARBOX SW]   │
│ [KEYPAD ×8]  └───────┘   [LIMIT SW ×2]  │
│ [STATUS LED]        [MAX98357A][SPKR]   │
└─────────────────────────────────────────┘
```

### Power

`USB bank → OnOff SHIM → +5V`, feeding Pi pins 2/4 and DRV8833 `VCC`.
`C1` 470µF polarised across the motor rail at the carrier. Pi pin 1 (`+3V3`)
feeds MCP23017 `VDD`. All grounds common; Pi pins 6/9/14/20/25/30/34/39 to
`GND`.

**Known tradeoff, accepted:** routing DRV8833 `VCC` downstream of the OnOff
SHIM puts motor current through the SHIM's load switch, rated around 2A. A 3V
motor stalled on a 5V rail can transiently exceed that, and the failure mode is
the SHIM cutting power — the Pi drops mid-action and it looks like a software
hang. Mitigated by the 470µF bulk cap, by capping `VERBOT_ACTION_SPEED` near
±60 (already a bring-up measurement), and by the existing stall watchdog. The
schematic carries this as a note so the current path is visible to anyone
reading it. Alternatives considered and rejected for now: tapping 5V upstream
of the SHIM (motor rail stays live at soft-off), or upstream plus a load switch
gated on `MOTOR_nSLEEP` (extra part).

### Motor

| Net | BCM | Pi pin | DRV8833 |
|---|---|---|---|
| `MOTOR_IN1` | 12 | 32 | `IN1` |
| `MOTOR_IN2` | 13 | 33 | `IN2` |
| `MOTOR_nSLEEP` | 6 | 31 | `EEP` |
| `MOTOR_nFAULT` | 16 | 36 | `ULT` |

`OUT1`/`OUT2` to `Motor:Motor_DC`. `IN3`/`IN4`/`OUT3`/`OUT4` get explicit
no-connect flags — channel B is unused.

Notes on the sheet: `EEP` may be tied to `VCC` by the carrier's `J1` solder
jumper, in which case set `VERBOT_MOTOR_SLEEP_PIN` to `null`; and swap
`OUT1`/`OUT2` rather than the code's sign convention if the motor runs
backwards, because interrogation must be the positive direction.

### Interrogation switches

`Verbot_Gearbox` block, one net per ribbon colour. White is the common ground
return. Inputs use the Pi's internal pull-ups and are active low.

| Colour | Order | Net | BCM | Pi pin |
|---|---|---|---|---|
| Purple | 1 | `SW_STOP` | 22 | 15 |
| Red | 2 | `SW_ROTATE_RIGHT` | 26 | 37 |
| Yellow | 3 | `SW_ROTATE_LEFT` | 10 | 19 |
| Grey | 4 | `SW_FORWARDS` | 9 | 21 |
| Blue | 5 | `SW_REVERSE` | 25 | 22 |
| Brown | 6 | `SW_PUT_DOWN` | 11 | 23 |
| Orange | 7 | `SW_PICK_UP` | 8 | 24 |
| Green | 8 | `SW_TALK` | 7 | 26 |

Matches `DEFAULT_SWITCH_PINS` in `src/verbot/config.py`.

The arm limit switches are drawn **inline** on Brown and Orange. `docs/hardware.md`
describes them in prose but no diagram has shown them, and they are why those
two lines can open mid-action — the signal the controller uses to stop.

### Audio

`+5V` to `VIN`. `I2S_BCLK` BCM18/pin12, `I2S_LRCLK` BCM19/pin35, `I2S_DIN`
BCM21/pin40. Speaker terminals to `Device:Speaker` (4–8Ω passive).

`SD` and `GAIN` no-connect. Sheet note ties `SD` to the `no-sdmode` flag in
`config/config.txt.example`: leaving it floating on the breakout's own pull-up
is what keeps BCM4 free for the OnOff SHIM.

### Front panel

`I2C_SDA` BCM2/pin3, `I2C_SCL` BCM3/pin5. `A0`/`A1`/`A2` to `GND` giving
address 0x20, matching `mcp23017_address`. `RESET` to `+3V3`. `INTA`/`INTB`
no-connect, with a note that `Mcp23017Keypad` polls at 50 Hz rather than using
the interrupt line.

`GPA0`–`GPA7` to eight `SW_Push` switches, common side to `GND`, in the
`BUTTON_ORDER` from `src/verbot/hardware/mcp23017.py`: Stop, Forwards, Reverse,
Rotate left, Rotate right, Pick up, Put down, Talk.

`GPB0` to `R1` 330Ω to the status LED to `GND`. 330Ω gives roughly 5mA from the
3.3V rail through a red LED; the MCP23017 sources up to 25mA per pin.

`GPB1`–`GPB7` no-connect. The driver configures them as inputs, so they float;
harmless, and noted on the sheet.

**Boxed note on the keypad**, reproducing the warning in `docs/hardware.md`:
this wiring assumes the eight buttons are independent switches to a common
rail. That has not been verified. The original PCB also carried the power
switching, so the intended approach is to bypass it and solder to the switch
contacts directly. Verify with a meter before wiring. `BUTTON_ORDER` is a guess
and may need reordering at bring-up.

## Verification

`kicad-cli sch erc` must report zero errors and zero warnings before commit.
This is a real check, not a formality — it catches unconnected pins, net-label
typos that silently split one net into two, and power-input pins with no
driving source. Those are the three mistakes that would cost bench time.

Reaching zero warnings requires:

- No-connect flags on `IN3` `IN4` `OUT3` `OUT4` `SD` `GAIN` `INTA` `INTB`
  `GPB1`–`GPB7`
- `PWR_FLAG` on `+5V`, `+3V3` and `GND` so ERC knows they are externally driven

Then `kicad-cli sch export pdf` and `kicad-cli sch export svg`.

No automated tests. This is a drawing; ERC is the verification. Actual ERC
output gets reported, not a claim that it passed.

## Out of scope

- PCB layout, footprints, fabrication outputs
- Drawing the internals of bought modules
- A matrix-wired keypad variant — would need a `decode_buttons()` rewrite
- Changes to any Python source
