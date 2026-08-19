# Verbot Hardware Reference

> **Attribution:** The electro-mechanical analysis and the interrogation switch
> wiring table in this document were reverse-engineered by **Neil Davis** for
> [neildavis/verbot-pi](https://github.com/neildavis/verbot-pi) (MIT licensed),
> partly from [Tomy's patent US4717364A](https://patents.google.com/patent/US4717364A/en).
> They are reproduced here with thanks. The GPIO assignments are new to this
> project and differ from the original.

> 📐 **Schematic:** [`hardware/verbot-schematic.pdf`](../hardware/verbot-schematic.pdf)
> ([SVG](../hardware/verbot-schematic.svg)) — the full wiring on one sheet.
> Source: [`hardware/verbot.kicad_sch`](../hardware/verbot.kicad_sch) (KiCad 10).
> The GPIO table below and `src/verbot/config.py` remain the pin-map source of
> truth; the schematic reproduces them.

> 🎨 **Wiring map:** [`hardware/wiring-map.html`](../hardware/wiring-map.html)
> — the same wiring drawn in colour, with the interrogation ribbon rendered in
> its actual conductor colours. Easier to work from at the bench than the
> schematic; open it in a browser.

## Electro-mechanical operation

Verbot is driven by a **single bi-directional 3V DC motor** and a set of
planetary gears. Motor direction selects between two mutually exclusive modes:

| Polarity | Motor direction | Operating mode |
|----------|-----------------|----------------|
| Normal   | Anti-clockwise  | Interrogation  |
| Reverse  | Clockwise       | Action         |

### Interrogation mode

The default on power-up. The motor turns a drum carrying 8 cams spaced equally
around its circumference. Each cam activates and then releases one of 8
normally-open switches in a fixed repeating sequence. Verbot performs no
visible movement in this mode — it is selecting which gear set is engaged.

The switches complete a circuit to ground, so their state is readable from
GPIO inputs.

### Action mode

Reversing the motor engages a clutch: the drum stops, leaving the last-selected
switch engaged, and a shaft inside the drum begins to turn. The planetary gear
set at the selected position performs its action, and continues until the motor
is reversed again.

The arm actions (`pick_up` / `put_down`) have mechanical **limit switches** in
series. When an arm reaches its travel limit the circuit breaks, which the
controller observes as the switch *releasing* while the action is running. That
is the signal to stop — without it the mechanism will strain against its stop.

### Putting it together

To perform action X: run the motor anti-clockwise until switch X activates,
then immediately reverse to clockwise. The gear set for X is now engaged and in
the correct position.

## Interrogation switch wiring

Nine-core ribbon cable from the gearbox switch bank to the original controller
board:

| Colour | Interrogation order | Action        |
|--------|---------------------|---------------|
| White  | N/A                 | GND — common return for all switches |
| Purple | 1                   | Stop          |
| Red    | 2                   | Rotate right  |
| Yellow | 3                   | Rotate left   |
| Grey   | 4                   | Move forward  |
| Blue   | 5                   | Move backward |
| Brown  | 6                   | Arms down / put down |
| Orange | 7                   | Arms up / pick up |
| Green  | 8                   | Talk          |

## Power supply

- **3V DC** (orange +ve, black ground) from 2× 'C' cells drove the motor. The
  original board reversed its polarity; the DRV8833 does that job now.
- **6V DC** (red +ve, black ground) from 4× 'AA' cells fed the original control
  board. That board and supply are removed; a 5V USB power bank feeds the Pi.
- Both rails were switched by the board behind the front keypad panel, which
  also fed the red power LED in the panel's bottom-right corner.

Where the motor's supply comes from now, and why it is not the Pi, is in
[Powering the motor](#powering-the-motor).

> ⚠️ The eight front-panel keypad buttons share that board with the power
> switching. Before wiring them to the MCP23017, trace whether they are
> independent switches to a common rail or a matrix, and decide what takes over
> the power-switch role (most likely the OnOff SHIM alone). It may be cleanest
> to bypass the original PCB and solder to the switch contacts directly.

## Original keypad behaviour

Each of the eight red buttons corresponded 1:1 to one of the eight actions. In
the original toy you *held a button down while speaking* the command you wanted
associated with it, and an LED blinked during program mode. This project
re-uses the 1:1 mapping; the hold gesture is available for re-binding.

## GPIO assignments (this project)

Raspberry Pi Zero 2 W, 64-bit Raspberry Pi OS (Trixie). **Bold** = Verbot
interrogation switches, inputs with pull-ups, active low.

| BCM | Physical | Used by | Function |
|-----|----------|---------|----------|
| 2   | 3        | I2C1    | SDA — MCP23017 |
| 3   | 5        | I2C1    | SCL — MCP23017 |
| 4   | 7        | OnOff SHIM | Shutdown |
| 6   | 31       | DRV8833 | nSLEEP (`EEP`) — high wakes the driver (output) |
| 7   | 26       | **Verbot** | SW Talk |
| 8   | 24       | **Verbot** | SW Pick up |
| 9   | 21       | **Verbot** | SW Forwards |
| 10  | 19       | **Verbot** | SW Rotate left |
| 11  | 23       | **Verbot** | SW Put down |
| 12  | 32       | DRV8833 | Motor `IN1` — kernel PWM0 |
| 13  | 33       | DRV8833 | Motor `IN2` — kernel PWM1 |
| 16  | 36       | DRV8833 | nFAULT (`ULT`) — active low, input with pull-up |
| 17  | 11       | OnOff SHIM | Power button |
| 18  | 12       | I2S DAC | BCLK |
| 19  | 35       | I2S DAC | LRCLK |
| 21  | 40       | I2S DAC | DIN |
| 22  | 15       | **Verbot** | SW Stop |
| 25  | 22       | **Verbot** | SW Reverse |
| 26  | 37       | **Verbot** | SW Rotate right |
| 27  | 13       | OnOff SHIM | LED |

Both PWM channels come from one overlay:
`dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4`.

**Free:** BCM 5, 14, 15, 20, 23, 24.

### Count the I2S pins twice

The three I2S lines sit next to pins that fail silently when you are off by one:

| Intended | Header | Neighbour | What you get |
|----------|--------|-----------|--------------|
| BCLK | 12 | **14 = GND** | Bit clock grounded. Dead silence. |
| DIN | 40 | **38 = BCM 20, PCM_DIN** | Data on the input pin. Dead silence. |
| LRCLK | 35 | 33 = BCM 13 (`IN2`) | Word clock into the motor driver. |

None of these produce an error anywhere. The overlay still registers the card,
`aplay -l` still lists the MAX98357A, `pinctrl get 18-21` still reports `a0` on
all three, and `/proc/asound/card0/pcm0p/sub0/hw_params` still reads `RUNNING`
with the pointer advancing. The Pi is genuinely clocking valid I2S — it is just
not arriving. Every software check passes while the speaker stays silent.

If there is no sound, check continuity from the header pin to the breakout pad
**before** touching the overlay. `speaker-test -D plughw:0,0` returning 0 says
nothing about whether audio reached the amp.

Confirm the amp is alive with a meter instead: SD_MODE on the Adafruit board
sits at ~0.45 V (its 1 MΩ pull-up against the chip's 100 kΩ internal pulldown),
which the MAX98357A reads as enabled in (L+R)/2 mono. Below 0.16 V is shutdown.
That reading also means `no-sdmode` is correct for this board — SD_MODE is not
wired to a GPIO, so the `sdmode` overlay variant does nothing here except claim
BCM 4, which the OnOff SHIM needs.

## DRV8833 carrier wiring

The common 12-pin breakout (`IN1`–`IN4`, `OUT1`–`OUT4`, `VCC`, `GND`, `EEP`,
`ULT`). Only channel A is used; `IN3`/`IN4`/`OUT3`/`OUT4` are left unconnected.

| Carrier pin | Goes to | Notes |
|-------------|---------|-------|
| `VCC` | Motor supply | **Not a logic rail.** The chip runs its own 3.3V regulator off this, so whatever you apply is what the motor sees. |
| `GND` | Pi GND | Common ground with the Pi is required for the logic to work. |
| `IN1` | BCM 12 | PWM0 |
| `IN2` | BCM 13 | PWM1 |
| `OUT1`/`OUT2` | Verbot motor | Polarity decides which sign is interrogation; swap if reversed. |
| `EEP` | BCM 6 | nSLEEP. The `J1` solder jumper ties this to `VCC` — check it with a meter. If it is bridged, set `VERBOT_MOTOR_SLEEP_PIN` to `null` and leave BCM 6 free. |
| `ULT` | BCM 16 | nFAULT, open-drain. Relies on the Pi's internal pull-up. |

Feeding `VCC` from 5V means the 3V motor sees 5V at full duty — cap
`VERBOT_ACTION_SPEED` accordingly rather than running it at ±100. Do **not**
feed `VCC` from the Pi's 3V3 pin to get a nominal 3V: that rail comes off the
PMIC and cannot supply a stalled motor.

There is no MODE pin. Unlike the DRV8835 this part has no PHASE/ENABLE mode,
which is why direction costs a second PWM channel rather than one GPIO.

### Powering the motor

**The Pi does not power the motor.** Two separate paths run through the carrier,
and confusing them is the usual source of "a GPIO can't supply that much
current":

- **Signal path** — `IN1`, `IN2` and `EEP` are high-impedance CMOS inputs
  drawing microamps. The Pi only says *which switches to close*. A GPIO sources
  around 16 mA, and the whole header perhaps 50 mA — orders of magnitude short
  of motor current, and it never has to supply it.
- **Power path** — motor current flows from `VCC`, through the H-bridge
  MOSFETs, out `OUT1`/`OUT2`, through the motor and back to `GND`. It never
  touches a GPIO.

That is why `VCC` is not a logic rail, and why the common ground still matters:
almost no current flows on that wire, but without a shared reference the carrier
cannot interpret the Pi's logic levels at all.

**Feed `VCC` from the power bank directly, not from the Pi's 5V header pin.**
The header pin is the USB input rail passed straight through, so it does work
electrically. But the motor then shares a supply with the Pi, and a stall or a
direction reversal can drag that rail down far enough to drop the Wi-Fi or reset
the board — and if the OnOff SHIM is inline, the motor current runs through the
SHIM as well. A 3V motor from 2× 'C' cells can pull on the order of an amp
stalled, which is not something to route through header pins.

Fit **100–470 µF** of electrolytic bulk capacitance across `VCC`/`GND` close to
the carrier. A direction reversal switches the full motor current in one step,
and without local storage that transient is taken straight out of the shared
supply.

| Pin | Feed from | Why |
|-----|-----------|-----|
| `VCC` | Power bank 5V | Not the Pi's 3V3 — that rail is off the PMIC and cannot supply a stalled motor |
| `GND` | Common with the Pi | Shared reference for the logic; carries no significant current |

Because `VCC` is 5V and the motor is a 3V part, cap `VERBOT_ACTION_SPEED` rather
than running ±100 — the PWM duty cycle is what keeps the average voltage near
the motor's rating.

### Notable changes from the original project

- **DRV8833 instead of a DRV8835**, driven as IN/IN on two kernel PWM channels
  (BCM 12 and 13) rather than one PWM plus a direction pin. Its `nSLEEP` and
  `nFAULT` lines are wired up; nothing reads the fault line yet.
- **Motor PWM is kernel-managed**, not pigpio. pigpio needs the PCM peripheral
  for DMA timing to leave hardware PWM available; the I2S DAC needs PCM too.
  Kernel PWM sidesteps that conflict entirely — the original project had to
  abandon hardware PWM for exactly this reason.
- **No AIY Voice Bonnet**, freeing BCM 14, 15, 16, 20, 23.
- Front-panel buttons and status LED hang off an **MCP23017** rather than
  consuming the last eight free GPIOs.
