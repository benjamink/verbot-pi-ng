# Deploying to the Pi Zero 2 W

## 1. Base image

Flash **64-bit Raspberry Pi OS (Trixie)**. The 64-bit build matters: it gets
prebuilt aarch64 wheels for `pydantic-core`, avoiding a from-source Rust build
that a Zero would struggle to finish.

## 2. Firmware config

Append the contents of [`../config/config.txt.example`](../config/config.txt.example)
to `/boot/firmware/config.txt` and reboot. Then verify:

```bash
ls /sys/class/pwm/          # expect pwmchip0
cat /sys/class/pwm/pwmchip0/npwm   # expect 2 — pwm-2chan gives both channels
aplay -l                    # expect the MAX98357A card
i2cdetect -y 1              # expect a device at 0x20
```

If `pwmchip0` is absent or numbered differently, set `VERBOT_PWM_CHIP`
accordingly — kernel PWM chip numbering has shifted between OS releases.

If `npwm` reads 1, the single-channel `pwm` overlay is still in place. The
DRV8833 needs `pwm-2chan`: it has no PHASE/ENABLE mode, so both IN1 and IN2
need a PWM channel.

## 3. System packages

```bash
sudo apt update
sudo apt install -y espeak-ng i2c-tools
espeak-ng "hello"      # confirm audio reaches the speaker
```

## 4. Install

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/benjamink/verbot-pi-ng.git ~/verbot-pi-ng
cd ~/verbot-pi-ng
uv sync --extra pi --frozen
```

`--frozen` installs exactly the committed `uv.lock`.

## 5. Permissions

```bash
sudo usermod -aG gpio,i2c,audio "$USER"
```

Log out and back in. If writes to `/sys/class/pwm` still fail, check for a udev
rule granting the `gpio` group access; add `/etc/udev/rules.d/99-pwm.rules` if
your image lacks one.

## 6. First run

```bash
VERBOT_USE_REAL_HARDWARE=true uv run verbot
```

Without that variable the server runs on fakes and the robot will not move —
useful for testing the API on any machine, confusing if you forget it on the Pi.
Look for the absence of the `running on fake hardware` warning in the log.

## 7. Service

```bash
sudo cp config/verbot.service /etc/systemd/system/verbot@.service
sudo systemctl daemon-reload
sudo systemctl enable --now verbot@$USER
journalctl -u verbot@$USER -f
```

Put `VERBOT_USE_REAL_HARDWARE=true` in a `.env` file in the working directory,
or add `Environment=` lines to the unit.

## Bring-up checklist

Work through these in order, with the robot **on a stand with its wheels off
the ground** until step 5 passes. Keep the schematic open while you do —
[`hardware/verbot-schematic.pdf`](../hardware/verbot-schematic.pdf)
([SVG](../hardware/verbot-schematic.svg)).

| # | Check | Command / action | Expected |
|---|-------|------------------|----------|
| 1 | Server healthy | `curl -s localhost:8080/healthz` | `{"status":"ok"}` |
| 2 | Switches read | Turn the drum **by hand**, watch `journalctl -f` | each switch logs a close then an open |
| 3 | Interrogation → action | `curl -X POST localhost:8080/actions/talk` | motor runs, then reverses when the talk switch closes |
| 4 | Stop | `curl -X POST localhost:8080/stop` | motor stops at the stop position |
| 5 | Limit switch | `curl -X POST localhost:8080/actions/pick_up` | arms rise and **stop at the top by themselves** |
| 6 | Speech | `curl -X POST localhost:8080/say -H 'content-type: application/json' -d '{"text":"I am Verbot"}'` | audible |
| 7 | Panel buttons | `sudo ip link set wlan0 down`, press each button | robot responds with no network |
| 8 | Watchdog | disconnect one switch, request that action | motor stops after the timeout, status shows `fault` |

If the motor never turns at all, check `EEP`/nSLEEP first: the DRV8833
tri-states its outputs until that pin is driven high, so a mis-wired sleep pin
looks exactly like a dead motor.

If the motor turns the wrong way, swap `OUT1` and `OUT2` rather than the sign
conventions in the code — interrogation must be the positive direction.

If step 3 never reverses, the switch wiring or polarity is wrong — check
`switch_event()` in `src/verbot/hardware/lgpio_switches.py` and confirm the pins
read low when closed.

If step 7 triggers the wrong action, reorder `BUTTON_ORDER` in
`src/verbot/hardware/mcp23017.py` to match the physical panel layout. The order
committed there is a guess.

## Values to measure and record

The defaults are inherited guesses. Replace them with measurements:

| Setting | Default | How to determine |
|---------|---------|------------------|
| `VERBOT_INTERROGATION_SPEED` | 50 | Lowest speed that still turns the drum reliably. Slower gives more reliable switch detection. |
| `VERBOT_INTERROGATION_TIMEOUT_S` | 10.0 | Time a full drum revolution takes, roughly doubled. |
| `VERBOT_ACTION_SPEED` | -100 | Reduce if the mechanism sounds strained. Also cap it if the DRV8833's `VCC` is fed from 5V: the motor is a 3V part, so ±60 is roughly its rated voltage. |
| `VERBOT_MOTOR_SLEEP_PIN` | 6 | Set to `null` if the carrier's `J1` jumper is bridged — nSLEEP is then tied high in hardware and BCM 6 stays free. |

Record what you land on here once measured.
