"""Front-panel keypad and status LED on an MCP23017 I2C expander.

Port A pins 0-7: the eight original red buttons, inputs with pull-ups, active
low. Port B pin 0: the red status LED in the panel's bottom-right corner.

Each button maps 1:1 to one of the eight gearbox actions, exactly as the
original toy did. Presses feed the same controller entry point the API uses, so
the panel keeps working with no network - which is the point of keeping it.

Polled at ~50 Hz rather than wired to the interrupt line: 20 ms is well below
perception and it saves a GPIO plus interrupt-clear handling.
"""

import asyncio
import logging
from typing import Any

from verbot.actions import Action
from verbot.config import Settings
from verbot.hardware.protocols import ButtonListener, LedPattern

log = logging.getLogger(__name__)

# MCP23017 registers (BANK=0)
IODIRA, IODIRB = 0x00, 0x01
GPPUA = 0x0C
GPIOA, GPIOB = 0x12, 0x13

LED_BIT = 0x01

# Physical left-to-right order of the buttons on the front panel.
# Verify against the real panel during bring-up and reorder if needed.
BUTTON_ORDER: tuple[Action, ...] = (
    Action.STOP,
    Action.FORWARDS,
    Action.REVERSE,
    Action.ROTATE_LEFT,
    Action.ROTATE_RIGHT,
    Action.PICK_UP,
    Action.PUT_DOWN,
    Action.TALK,
)

BLINK_PERIODS = {
    LedPattern.SLOW_BLINK: 0.5,
    LedPattern.FAST_BLINK: 0.12,
}


def decode_buttons(previous: int, current: int) -> list[Action]:
    """Return the buttons that went from released to pressed.

    Inputs are active low, so a press is a 1 -> 0 transition. Comparing against
    the previous sample means holding a button reports one press, not one per
    poll.
    """
    newly_pressed = previous & ~current
    return [BUTTON_ORDER[bit] for bit in range(8) if newly_pressed & (1 << bit)]


def open_bus(settings: Settings) -> Any:
    import smbus2

    return smbus2.SMBus(settings.i2c_bus)


class Mcp23017Keypad:
    def __init__(self, settings: Settings, bus: Any) -> None:
        self._settings = settings
        self._bus = bus
        self._addr = settings.mcp23017_address
        self._listener: ButtonListener | None = None
        self._previous = 0xFF
        self._task: asyncio.Task[None] | None = None

    def set_listener(self, listener: ButtonListener) -> None:
        self._listener = listener

    async def start(self) -> None:
        self._bus.write_byte_data(self._addr, IODIRA, 0xFF)  # port A all inputs
        self._bus.write_byte_data(self._addr, GPPUA, 0xFF)  # with pull-ups
        self._previous = self._bus.read_byte_data(self._addr, GPIOA)
        self._task = asyncio.create_task(self._poll_loop())
        log.info("keypad polling at %.0f Hz", self._settings.keypad_poll_hz)

    async def poll_once(self) -> None:
        current = self._bus.read_byte_data(self._addr, GPIOA)
        pressed = decode_buttons(self._previous, current)
        self._previous = current
        for action in pressed:
            log.info("front panel button: %s", action)
            if self._listener is not None:
                await self._listener(action)

    async def _poll_loop(self) -> None:
        interval = 1.0 / self._settings.keypad_poll_hz
        while True:
            try:
                await self.poll_once()
            except OSError as exc:
                log.warning("keypad read failed: %s", exc)
            await asyncio.sleep(interval)

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None


class Mcp23017Led:
    def __init__(self, settings: Settings, bus: Any) -> None:
        self._bus = bus
        self._addr = settings.mcp23017_address
        self._pattern = LedPattern.OFF
        self._task: asyncio.Task[None] | None = None
        # GPB0 output, rest inputs.
        self._bus.write_byte_data(self._addr, IODIRB, 0xFF & ~LED_BIT)

    async def set_pattern(self, pattern: LedPattern) -> None:
        if pattern is self._pattern:
            return
        self._pattern = pattern
        self._cancel_blink()

        if pattern is LedPattern.OFF:
            self._write(False)
        elif pattern is LedPattern.SOLID:
            self._write(True)
        else:
            self._task = asyncio.create_task(self._blink(BLINK_PERIODS[pattern]))

    async def _blink(self, period: float) -> None:
        on = False
        while True:
            on = not on
            self._write(on)
            await asyncio.sleep(period)

    def _write(self, on: bool) -> None:
        value = self._bus.read_byte_data(self._addr, GPIOB)
        value = (value | LED_BIT) if on else (value & ~LED_BIT)
        self._bus.write_byte_data(self._addr, GPIOB, value)

    def _cancel_blink(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def close(self) -> None:
        self._cancel_blink()
        self._write(False)
