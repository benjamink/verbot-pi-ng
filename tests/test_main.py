from verbot.config import Settings
from verbot.hardware.fakes import FakeKeypad, FakeLed, FakeMotor, FakeSwitchBank
from verbot.main_support import build_hardware, build_keypad


def test_build_hardware_returns_fakes_when_hardware_disabled():
    motor, switches = build_hardware(Settings(use_real_hardware=False))
    assert isinstance(motor, FakeMotor)
    assert isinstance(switches, FakeSwitchBank)


def test_build_keypad_returns_fakes_when_hardware_disabled():
    keypad, led = build_keypad(Settings(use_real_hardware=False))
    assert isinstance(keypad, FakeKeypad)
    assert isinstance(led, FakeLed)


def test_build_keypad_returns_none_when_disabled():
    keypad, led = build_keypad(Settings(keypad_enabled=False))
    assert keypad is None
    assert led is None
