"""Hardware selection, split out from __main__ so it is importable in tests."""

import logging

from verbot.config import Settings
from verbot.hardware.protocols import Keypad, MotorDriver, StatusLed, SwitchBank

log = logging.getLogger(__name__)


def build_hardware(settings: Settings) -> tuple[MotorDriver, SwitchBank]:
    """Return real adapters on the Pi, fakes everywhere else.

    The lgpio imports live inside this branch so dev machines never need them.
    """
    if not settings.use_real_hardware:
        from verbot.hardware.fakes import FakeMotor, FakeSwitchBank

        log.warning("VERBOT_USE_REAL_HARDWARE is false - running on fake hardware")
        return FakeMotor(), FakeSwitchBank()

    from verbot.hardware.lgpio_switches import LgpioSwitchBank
    from verbot.hardware.pwm_motor import KernelPwmMotor

    return KernelPwmMotor(settings), LgpioSwitchBank(settings)


def build_keypad(settings: Settings) -> tuple[Keypad | None, StatusLed | None]:
    """Return (keypad, led), or (None, None) when the keypad is disabled."""
    if not settings.keypad_enabled:
        return None, None

    if not settings.use_real_hardware:
        from verbot.hardware.fakes import FakeKeypad, FakeLed

        return FakeKeypad(), FakeLed()

    from verbot.hardware.mcp23017 import Mcp23017Keypad, Mcp23017Led, open_bus

    bus = open_bus(settings)
    return Mcp23017Keypad(settings, bus=bus), Mcp23017Led(settings, bus=bus)
