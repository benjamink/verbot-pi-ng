from verbot.actions import Action
from verbot.config import DEFAULT_SWITCH_PINS
from verbot.hardware.lgpio_switches import switch_event

PIN_TO_ACTION = {pin: action for action, pin in DEFAULT_SWITCH_PINS.items()}


def test_low_level_means_the_switch_closed():
    """Pins are pulled up; the cam closes the switch to ground."""
    assert switch_event(PIN_TO_ACTION, pin=9, level=0) == (Action.FORWARDS, True)


def test_high_level_means_the_switch_opened():
    assert switch_event(PIN_TO_ACTION, pin=9, level=1) == (Action.FORWARDS, False)


def test_watchdog_level_is_ignored():
    """lgpio reports level 2 for a watchdog timeout, which is not an edge."""
    assert switch_event(PIN_TO_ACTION, pin=9, level=2) is None


def test_unknown_pin_is_ignored():
    assert switch_event(PIN_TO_ACTION, pin=99, level=0) is None


def test_every_action_maps_to_a_distinct_pin():
    assert len(PIN_TO_ACTION) == len(Action)


def test_every_switch_pin_translates_both_ways():
    for pin, action in PIN_TO_ACTION.items():
        assert switch_event(PIN_TO_ACTION, pin, 0) == (action, True)
        assert switch_event(PIN_TO_ACTION, pin, 1) == (action, False)
