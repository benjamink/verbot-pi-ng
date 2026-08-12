"""The Verbot interrogation/action state machine.

Verbot has one bi-directional motor. Running it anti-clockwise rotates a cam
drum that closes each of eight switches in turn (interrogation). Reversing to
clockwise stops the drum and drives the gear set at whichever switch is closed
(action). So performing action X means: interrogate until X closes, then
reverse. See docs/hardware.md.
"""

import asyncio
import logging

from verbot.actions import Action, ControllerStatus, Mode
from verbot.config import Settings
from verbot.hardware.protocols import MotorDriver, SwitchBank

log = logging.getLogger(__name__)


class Controller:
    def __init__(
        self,
        motor: MotorDriver,
        switches: SwitchBank,
        settings: Settings,
    ) -> None:
        self._motor = motor
        self._switches = switches
        self._settings = settings
        self._mode = Mode.IDLE
        self._current: Action | None = None
        self._desired: Action | None = None
        self._timeout_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self._motor.open()
        self._switches.set_listener(self.handle_switch_event)
        await self._switches.start()
        await self._motor.set_speed_percent(0)

    async def close(self) -> None:
        self._cancel_timeout()
        await self._motor.set_speed_percent(0)
        await self._motor.close()
        await self._switches.close()

    @property
    def status(self) -> ControllerStatus:
        return ControllerStatus(
            mode=self._mode,
            current_action=self._current,
            desired_action=self._desired,
        )

    async def request_action(self, action: Action) -> None:
        """Begin interrogating for `action`. Returns as soon as the motor starts."""
        if self._mode is Mode.ACTING and action is self._current:
            log.debug("%s already running, ignoring request", action)
            return
        # Only skip a STOP if we know the drum is already parked there. At boot
        # `_current` is None: the motor is off but the drum position is
        # unknown, so an explicit stop still homes the mechanism.
        if self._mode is Mode.IDLE and action is Action.STOP and self._current is Action.STOP:
            log.debug("already parked at stop, ignoring request")
            return

        log.info("interrogating for %s", action)
        self._desired = action
        self._mode = Mode.INTERROGATING
        self._start_timeout()
        await self._motor.set_speed_percent(self._settings.interrogation_speed)

    async def handle_switch_event(self, action: Action, activated: bool) -> None:
        """Called by the switch bank whenever a gearbox switch opens or closes.

        Two events matter, and mode disambiguates them:

        * INTERROGATING + closed + it is the one we want -> gears are in
          position, start the action.
        * ACTING + opened + it is the action we are running -> a mechanical
          limit switch broke the circuit, so the action is finished.

        Everything else is the drum sweeping past, and is ignored.
        """
        if self._mode is Mode.INTERROGATING and activated and action is self._desired:
            await self._enter_action(action)
        elif self._mode is Mode.ACTING and not activated and action is self._current:
            log.info("limit switch reached for %s", action)
            await self.request_action(Action.STOP)

    async def _enter_action(self, action: Action) -> None:
        self._cancel_timeout()
        self._current = action
        self._desired = None

        if action is Action.STOP:
            log.info("reached stop position")
            self._mode = Mode.IDLE
            await self._motor.set_speed_percent(0)
        else:
            log.info("gears in position, performing %s", action)
            self._mode = Mode.ACTING
            await self._motor.set_speed_percent(self._settings.action_speed)

    def _start_timeout(self) -> None:
        self._cancel_timeout()
        self._timeout_task = asyncio.create_task(self._interrogation_timeout())

    def _cancel_timeout(self) -> None:
        if self._timeout_task is not None:
            self._timeout_task.cancel()
            self._timeout_task = None

    async def _interrogation_timeout(self) -> None:
        await asyncio.sleep(self._settings.interrogation_timeout_s)
        log.error("interrogation timed out waiting for %s - stopping motor", self._desired)
        self._mode = Mode.FAULT
        self._desired = None
        await self._motor.set_speed_percent(0)
