import asyncio

import pytest

from verbot.actions import Action, Mode
from verbot.config import Settings
from verbot.controller import Controller
from verbot.hardware.fakes import FakeMotor, FakeSwitchBank


@pytest.fixture
def settings() -> Settings:
    return Settings(interrogation_timeout_s=0.05)


@pytest.fixture
async def rig(settings):
    motor, switches = FakeMotor(), FakeSwitchBank()
    controller = Controller(motor=motor, switches=switches, settings=settings)
    await controller.start()
    yield controller, motor, switches
    await controller.close()


async def test_starts_idle_with_motor_stopped(rig):
    controller, motor, _ = rig
    assert controller.status.mode is Mode.IDLE
    assert motor.speed == 0


async def test_request_begins_interrogation(rig):
    controller, motor, _ = rig
    await controller.request_action(Action.FORWARDS)
    assert controller.status.mode is Mode.INTERROGATING
    assert controller.status.desired_action is Action.FORWARDS
    assert motor.speed == 50


async def test_reaching_target_switch_reverses_motor(rig):
    controller, motor, switches = rig
    await controller.request_action(Action.FORWARDS)
    await switches.sweep_to(Action.FORWARDS)

    assert controller.status.mode is Mode.ACTING
    assert controller.status.current_action is Action.FORWARDS
    assert controller.status.desired_action is None
    assert motor.speed == -100


async def test_intermediate_switches_do_not_trigger_the_action(rig):
    """The drum closes STOP, ROTATE_RIGHT and ROTATE_LEFT on the way to
    FORWARDS. None of them may start an action."""
    controller, motor, switches = rig
    await controller.request_action(Action.FORWARDS)

    for action in (Action.STOP, Action.ROTATE_RIGHT, Action.ROTATE_LEFT):
        await switches.activate(action)
        assert controller.status.mode is Mode.INTERROGATING, f"{action} broke interrogation"
        await switches.release(action)

    assert motor.speed == 50


async def test_stop_halts_the_motor_at_the_stop_position(rig):
    controller, motor, switches = rig
    await controller.request_action(Action.STOP)
    await switches.sweep_to(Action.STOP)

    assert controller.status.mode is Mode.IDLE
    assert controller.status.current_action is Action.STOP
    assert motor.speed == 0


async def test_requesting_the_running_action_is_a_no_op(rig):
    controller, motor, switches = rig
    await controller.request_action(Action.FORWARDS)
    await switches.sweep_to(Action.FORWARDS)
    history_before = list(motor.speed_history)

    await controller.request_action(Action.FORWARDS)

    assert motor.speed_history == history_before
    assert controller.status.mode is Mode.ACTING


async def test_switching_actions_re_enters_interrogation(rig):
    controller, motor, switches = rig
    await controller.request_action(Action.FORWARDS)
    await switches.sweep_to(Action.FORWARDS)

    await controller.request_action(Action.TALK)
    assert controller.status.mode is Mode.INTERROGATING
    assert motor.speed == 50

    await switches.release(Action.FORWARDS)
    await switches.activate(Action.TALK)
    assert controller.status.mode is Mode.ACTING
    assert motor.speed == -100


async def test_limit_switch_stops_a_running_action(rig):
    """PICK_UP runs until the arm hits its stop, which breaks the switch
    circuit. The controller must interrogate back to STOP."""
    controller, motor, switches = rig
    await controller.request_action(Action.PICK_UP)
    await switches.sweep_to(Action.PICK_UP)
    assert controller.status.mode is Mode.ACTING

    await switches.release(Action.PICK_UP)

    assert controller.status.mode is Mode.INTERROGATING
    assert controller.status.desired_action is Action.STOP
    assert motor.speed == 50

    await switches.activate(Action.STOP)
    assert controller.status.mode is Mode.IDLE
    assert motor.speed == 0


async def test_release_of_an_unrelated_switch_is_ignored(rig):
    controller, motor, switches = rig
    await controller.request_action(Action.FORWARDS)
    await switches.sweep_to(Action.FORWARDS)

    await switches.release(Action.TALK)

    assert controller.status.mode is Mode.ACTING
    assert motor.speed == -100


async def test_interrogation_timeout_stops_the_motor(rig):
    """A dirty switch that never closes must not leave the motor running."""
    controller, motor, _ = rig
    await controller.request_action(Action.TALK)
    assert motor.speed == 50

    await asyncio.sleep(0.1)  # settings.interrogation_timeout_s is 0.05

    assert controller.status.mode is Mode.FAULT
    assert controller.status.desired_action is None
    assert motor.speed == 0


async def test_reaching_the_switch_cancels_the_timeout(rig):
    controller, motor, switches = rig
    await controller.request_action(Action.FORWARDS)
    await switches.sweep_to(Action.FORWARDS)

    await asyncio.sleep(0.1)

    assert controller.status.mode is Mode.ACTING
    assert motor.speed == -100


async def test_a_new_request_clears_a_fault(rig):
    controller, motor, switches = rig
    await controller.request_action(Action.TALK)
    await asyncio.sleep(0.1)
    assert controller.status.mode is Mode.FAULT

    await controller.request_action(Action.FORWARDS)
    assert controller.status.mode is Mode.INTERROGATING

    await switches.sweep_to(Action.FORWARDS)
    assert controller.status.mode is Mode.ACTING


async def test_close_always_stops_the_motor(settings):
    motor, switches = FakeMotor(), FakeSwitchBank()
    controller = Controller(motor=motor, switches=switches, settings=settings)
    await controller.start()
    await controller.request_action(Action.FORWARDS)
    assert motor.speed == 50

    await controller.close()

    assert motor.speed == 0
    assert motor.closed and switches.closed
