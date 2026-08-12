# Verbot Hardware Reference

> **Attribution:** The electro-mechanical analysis and the interrogation switch
> wiring table in this document were reverse-engineered by **Neil Davis** for
> [neildavis/verbot-pi](https://github.com/neildavis/verbot-pi) (MIT licensed),
> partly from [Tomy's patent US4717364A](https://patents.google.com/patent/US4717364A/en).
> They are reproduced here with thanks. The GPIO assignments are new to this
> project and differ from the original.

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
  original board reversed its polarity; the DRV8835 does that job now.
- **6V DC** (red +ve, black ground) from 4× 'AA' cells fed the original control
  board. That board and supply are removed; a 5V USB power bank feeds the Pi.
- Both rails were switched by the board behind the front keypad panel, which
  also fed the red power LED in the panel's bottom-right corner.

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
| 5   | 29       | DRV8835 | Motor M1 direction (output) |
| 7   | 26       | **Verbot** | SW Talk |
| 8   | 24       | **Verbot** | SW Pick up |
| 9   | 21       | **Verbot** | SW Forwards |
| 10  | 19       | **Verbot** | SW Rotate left |
| 11  | 23       | **Verbot** | SW Put down |
| 12  | 32       | DRV8835 | Motor M1 PWM — kernel PWM0 (`dtoverlay=pwm,pin=12,func=4`) |
| 17  | 11       | OnOff SHIM | Power button |
| 18  | 12       | I2S DAC | BCLK |
| 19  | 35       | I2S DAC | LRCLK |
| 21  | 40       | I2S DAC | DIN |
| 22  | 15       | **Verbot** | SW Stop |
| 25  | 22       | **Verbot** | SW Reverse |
| 26  | 37       | **Verbot** | SW Rotate right |
| 27  | 13       | OnOff SHIM | LED |

**Free:** BCM 6, 13, 14, 15, 16, 20, 23, 24.

### Notable changes from the original project

- **Motor moved from the DRV8835 M2 channel to M1** (PWM 13→12, DIR 6→5). M1's
  PWM pin is PWM0, which the single-channel `pwm` overlay drives directly.
- **Motor PWM is kernel-managed**, not pigpio. pigpio needs the PCM peripheral
  for DMA timing to leave hardware PWM available; the I2S DAC needs PCM too.
  Kernel PWM sidesteps that conflict entirely — the original project had to
  abandon hardware PWM for exactly this reason.
- **No AIY Voice Bonnet**, freeing BCM 14, 15, 16, 20, 23.
- Front-panel buttons and status LED hang off an **MCP23017** rather than
  consuming the last eight free GPIOs.
