# verbot-pi-ng

Control a 1984 [Tomy Verbot](https://www.theoldrobots.com/verbot.html) toy robot
from a Raspberry Pi Zero 2 W, over an HTTP API, with speech output.

The original 1980s electronics are removed; the motor, gearbox, interrogation
switch bank and front keypad are all retained and driven directly from the Pi.

New here? [**Theory of operation**](docs/theory-of-operation.md) explains how
the gearbox works, what replaced the original electronics, how the software
drives it, and the API.

## Status

**Software complete and tested off-Pi; not yet run on the robot.**

The state machine, HTTP API, speech, keypad and both hardware adapters are
implemented with a passing test suite that needs no Raspberry Pi. What remains
is physical bring-up: wiring the MCP23017 to the front panel, and working
through the [bring-up checklist](docs/deployment.md#bring-up-checklist) to
verify switch polarity, button order and the measured motor speeds.

See [the implementation plan](docs/superpowers/plans/2026-08-11-verbot-pi-ng.md)
for the task breakdown and deferred phase 2 work.

## Features

- **HTTP API** (FastAPI) for every robot action, with OpenAPI docs at `/docs`
- **Speech output** via an I2S DAC and `espeak-ng`
- **Front-panel keypad** — the original eight buttons drive the robot directly,
  with no network required
- **Status LED** in the original panel position
- **Optional shutdown endpoint** — power the Pi down over HTTP, guarded by a
  token and off by default
- **Zeroconf/mDNS** service advertisement for discovery
- **Runs its full test suite off-Pi** — hardware sits behind protocols with
  in-memory fakes

## Hardware

- Tomy Verbot with working motor and gearbox
- Raspberry Pi Zero 2 W (64-bit Raspberry Pi OS, Trixie)
- DRV8833 dual motor driver carrier
- MAX98357A I2S DAC/amplifier + small **passive** speaker (4–8Ω)
- MCP23017 I2C GPIO expander (front-panel buttons + status LED)
- [Pimoroni OnOff SHIM](https://shop.pimoroni.com/products/onoff-shim)
- 5V USB power bank

Wiring, pin assignments and the reverse-engineered mechanical details are in
[`docs/hardware.md`](docs/hardware.md), and the full schematic is at
[`hardware/verbot-schematic.pdf`](hardware/verbot-schematic.pdf)
([SVG](hardware/verbot-schematic.svg)). For bench work,
[`hardware/wiring-map.html`](hardware/wiring-map.html) draws the same wiring in
colour, with the interrogation ribbon in its actual conductor colours.

## Development

Requires [uv](https://docs.astral.sh/uv/). No Raspberry Pi needed — the
hardware layer has fakes.

```bash
uv sync            # create .venv and install deps
uv run pytest      # run the test suite
uv run ruff check  # lint
```

## Running on the Pi

```bash
sudo apt install espeak-ng
uv sync --extra pi --frozen
VERBOT_USE_REAL_HARDWARE=true uv run verbot
```

Full instructions, including the required device tree overlays and the
bring-up checklist, are in [`docs/deployment.md`](docs/deployment.md).

## Credits

The hardware reverse-engineering this project depends on was done by
**Neil Davis** for [neildavis/verbot-pi](https://github.com/neildavis/verbot-pi).
This is an independent rewrite targeting different hardware, without the Google
Assistant integration. See [LICENSE](LICENSE).

## License

MIT
