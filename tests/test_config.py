from verbot.actions import Action, ControllerStatus, Mode
from verbot.config import Settings


def test_every_action_has_a_switch_pin():
    settings = Settings()
    assert set(settings.switch_pins) == set(Action)


def test_switch_pins_are_unique():
    settings = Settings()
    pins = list(settings.switch_pins.values())
    assert len(pins) == len(set(pins))


def test_motor_pins_do_not_collide_with_switch_pins():
    settings = Settings()
    switch_pins = set(settings.switch_pins.values())
    assert settings.motor_sleep_pin not in switch_pins
    assert settings.motor_fault_pin not in switch_pins
    assert settings.motor_sleep_pin != settings.motor_fault_pin


def test_motor_pwm_channels_differ():
    """One channel per DRV8833 input; sharing one would fix the direction."""
    settings = Settings()
    assert settings.pwm_channel_a != settings.pwm_channel_b


def test_speeds_have_opposing_signs():
    settings = Settings()
    assert settings.interrogation_speed > 0
    assert settings.action_speed < 0


def test_settings_read_from_env(monkeypatch):
    monkeypatch.setenv("VERBOT_PORT", "9999")
    assert Settings().port == 9999


def test_status_serialises_to_plain_strings():
    status = ControllerStatus(mode=Mode.ACTING, current_action=Action.FORWARDS, desired_action=None)
    assert status.model_dump() == {
        "mode": "acting",
        "current_action": "forwards",
        "desired_action": None,
    }
