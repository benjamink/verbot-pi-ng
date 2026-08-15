"""Protocols every hardware adapter implements.

The controller depends only on these, which is what lets the entire state
machine be tested with no Raspberry Pi attached.
"""

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Protocol

from verbot.actions import Action

SwitchListener = Callable[[Action, bool], Awaitable[None]]
ButtonListener = Callable[[Action], Awaitable[None]]


class LedPattern(StrEnum):
    OFF = "off"
    SOLID = "solid"
    SLOW_BLINK = "slow_blink"
    FAST_BLINK = "fast_blink"


class MotorDriver(Protocol):
    async def open(self) -> None:
        """Acquire the PWM channels and wake the driver. Called once at startup."""

    async def set_speed_percent(self, percent: int) -> None:
        """Set speed as a percentage in [-100, 100]. Sign selects direction."""

    async def read_fault(self) -> bool:
        """True while the driver reports a hardware fault. Nothing consumes this
        yet; the controller still infers trouble from switch timeouts."""

    async def close(self) -> None: ...


class SwitchBank(Protocol):
    """The eight interrogation switches inside the gearbox.

    Adapters translate electrical level to `activated`: True means the switch
    is closed (the cam is holding it down), regardless of pull-up polarity.
    """

    def set_listener(self, listener: SwitchListener) -> None: ...
    async def start(self) -> None: ...
    async def close(self) -> None: ...


class Keypad(Protocol):
    """The eight red buttons on the original front panel."""

    def set_listener(self, listener: ButtonListener) -> None: ...
    async def start(self) -> None: ...
    async def close(self) -> None: ...


class StatusLed(Protocol):
    async def set_pattern(self, pattern: LedPattern) -> None: ...
    async def close(self) -> None: ...


class SpeechEngine(Protocol):
    async def say(self, text: str) -> None: ...
    async def close(self) -> None: ...
