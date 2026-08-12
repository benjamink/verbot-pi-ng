from verbot.actions import Action, ControllerStatus, Mode
from verbot.config import Settings


def test_every_action_has_a_switch_pin():
    settings = Settings()
    assert set(settings.switch_pins) == set(Action)


def test_switch_pins_are_unique():
    settings = Settings()
    pins = list(settings.switch_pins.values())
    assert len(pins) == len(set(pins))
    assert settings.motor_dir_pin not in pins


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
