# verbot-pi-ng

Control a 1984 [Tomy Verbot](https://www.theoldrobots.com/verbot.html) toy robot
from a Raspberry Pi Zero 2 W, over an HTTP API, with speech output.

The original 1980s electronics are removed; the motor, gearbox, interrogation
switch bank and front keypad are all retained and driven directly from the Pi.

## Status

Early development. See [the implementation plan](docs/superpowers/plans/2026-08-11-verbot-pi-ng.md).

## Features

- **HTTP API** (FastAPI) for every robot action, with OpenAPI docs at `/docs`
- **Speech output** via an I2S DAC and `espeak-ng`
- **Front-panel keypad** — the original eight buttons drive the robot directly,
  with no network required
- **Status LED** in the original panel position
- **Zeroconf/mDNS** service advertisement for discovery
- **Runs its full test suite off-Pi** — hardware sits behind protocols with
  in-memory fakes

## Hardware

- Tomy Verbot with working motor and gearbox
- Raspberry Pi Zero 2 W (64-bit Raspberry Pi OS, Trixie)
- [Pololu DRV8835 dual motor driver](https://www.pololu.com/product/2753)
- MAX98357A I2S DAC/amplifier + small speaker
- MCP23017 I2C GPIO expander (front-panel buttons + status LED)
- [Pimoroni OnOff SHIM](https://shop.pimoroni.com/products/onoff-shim)
- 5V USB power bank

Wiring, pin assignments and the reverse-engineered mechanical details are in
[`docs/hardware.md`](docs/hardware.md).

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
uv sync --extra pi
uv run verbot
```

See [`config/config.txt.example`](config/config.txt.example) for the required
device tree overlays and [`config/verbot.service`](config/verbot.service) for
the systemd unit.

## Credits

The hardware reverse-engineering this project depends on was done by
**Neil Davis** for [neildavis/verbot-pi](https://github.com/neildavis/verbot-pi).
This is an independent rewrite targeting different hardware, without the Google
Assistant integration. See [LICENSE](LICENSE).

## License

MIT
