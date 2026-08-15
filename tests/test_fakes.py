import pytest

from verbot.actions import Action
from verbot.hardware.fakes import FakeMotor, FakeSwitchBank


async def test_fake_motor_records_history():
    motor = FakeMotor()
    await motor.set_speed_percent(50)
    await motor.set_speed_percent(-100)
    assert motor.speed == -100
    assert motor.speed_history == [50, -100]


async def test_fake_motor_rejects_out_of_range():
    motor = FakeMotor()
    with pytest.raises(ValueError):
        await motor.set_speed_percent(101)


async def test_fake_switch_bank_notifies_listener():
    events: list[tuple[Action, bool]] = []
    bank = FakeSwitchBank()

    async def listener(action: Action, activated: bool) -> None:
        events.append((action, activated))

    bank.set_listener(listener)
    await bank.start()
    await bank.activate(Action.FORWARDS)
    await bank.release(Action.FORWARDS)

    assert events == [(Action.FORWARDS, True), (Action.FORWARDS, False)]


async def test_sweep_to_visits_every_position_in_drum_order():
    """The real drum activates and releases each switch in turn until it
    reaches the target. sweep_to reproduces that so tests exercise the
    filtering logic rather than a single convenient event."""
    events: list[tuple[Action, bool]] = []
    bank = FakeSwitchBank()

    async def listener(action: Action, activated: bool) -> None:
        events.append((action, activated))

    bank.set_listener(listener)
    await bank.start()
    await bank.sweep_to(Action.FORWARDS)

    # STOP, ROTATE_RIGHT, ROTATE_LEFT each activate then release; FORWARDS
    # activates and stays closed.
    assert events == [
        (Action.STOP, True),
        (Action.STOP, False),
        (Action.ROTATE_RIGHT, True),
        (Action.ROTATE_RIGHT, False),
        (Action.ROTATE_LEFT, True),
        (Action.ROTATE_LEFT, False),
        (Action.FORWARDS, True),
    ]


async def test_fake_motor_reports_no_fault_by_default():
    motor = FakeMotor()
    assert await motor.read_fault() is False


async def test_fake_motor_reports_an_injected_fault():
    """Lets the controller's fault handling be exercised without a real driver."""
    motor = FakeMotor()
    motor.fault = True
    assert await motor.read_fault() is True
