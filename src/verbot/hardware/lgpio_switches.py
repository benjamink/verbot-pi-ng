"""Interrogation switch bank via lgpio.

Switch pins are inputs with pull-ups. A cam closing the switch pulls the pin to
ground, so level 0 means activated. The controller never sees voltage levels -
`switch_event` translates them to the domain's `activated` flag.

lgpio calls back on its own thread, so every event is handed to the asyncio
loop with `call_soon_threadsafe`. The controller therefore only ever runs on
the loop and needs no locking.
"""

import asyncio
import logging

from verbot.actions import Action
from verbot.config import Settings
from verbot.hardware.protocols import SwitchListener

log = logging.getLogger(__name__)

LEVEL_LOW = 0
LEVEL_HIGH = 1
LEVEL_WATCHDOG = 2


def switch_event(
    pin_to_action: dict[int, Action], pin: int, level: int
) -> tuple[Action, bool] | None:
    """Translate an lgpio alert into a domain event, or None to ignore it."""
    action = pin_to_action.get(pin)
    if action is None:
        return None
    if level == LEVEL_LOW:
        return (action, True)
    if level == LEVEL_HIGH:
        return (action, False)
    return None  # watchdog timeout, not an edge


class LgpioSwitchBank:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pin_to_action = {pin: action for action, pin in settings.switch_pins.items()}
        self._listener: SwitchListener | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._handle: int | None = None
        self._callbacks: list[object] = []
        self._tasks: set[asyncio.Task[None]] = set()

    def set_listener(self, listener: SwitchListener) -> None:
        self._listener = listener

    async def start(self) -> None:
        import lgpio

        self._loop = asyncio.get_running_loop()
        self._handle = lgpio.gpiochip_open(0)

        for pin in self._pin_to_action:
            lgpio.gpio_claim_alert(self._handle, pin, lgpio.BOTH_EDGES, lgpio.SET_PULL_UP)
            lgpio.gpio_set_debounce_micros(self._handle, pin, self._settings.switch_debounce_us)
            self._callbacks.append(
                lgpio.callback(self._handle, pin, lgpio.BOTH_EDGES, self._on_alert)
            )

        log.info("watching %d interrogation switches", len(self._pin_to_action))

    def _on_alert(self, chip: int, pin: int, level: int, timestamp: int) -> None:
        """Runs on lgpio's thread. Do nothing here but hand off to the loop."""
        event = switch_event(self._pin_to_action, pin, level)
        if event is None or self._listener is None or self._loop is None:
            return
        action, activated = event
        self._loop.call_soon_threadsafe(self._dispatch, action, activated)

    def _dispatch(self, action: Action, activated: bool) -> None:
        if self._listener is None:
            return
        # Keep a reference so the task is not garbage collected mid-flight.
        task = asyncio.create_task(self._listener(action, activated))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        import lgpio

        for cb in self._callbacks:
            cb.cancel()
        self._callbacks.clear()
        if self._handle is not None:
            lgpio.gpiochip_close(self._handle)
            self._handle = None
