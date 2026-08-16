import asyncio

from httpx import ASGITransport, AsyncClient

from verbot.__main__ import build_app
from verbot.actions import Action, Mode
from verbot.config import Settings
from verbot.hardware.fakes import FakeKeypad, FakeLed, FakeMotor, FakePower, FakeSwitchBank
from verbot.hardware.protocols import LedPattern
from verbot.main_support import build_hardware, build_keypad, build_power


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


async def test_lifespan_wires_the_keypad_to_the_controller():
    """A front-panel press must drive the robot without going near the API.

    httpx's ASGITransport does not run the lifespan protocol, so drive it
    directly - that is where the keypad gets wired up.
    """
    app = build_app(Settings())

    async with app.router.lifespan_context(app):
        keypad, led = app.state.keypad, app.state.led
        assert led.pattern is LedPattern.SOLID

        await keypad.press(Action.FORWARDS)
        assert app.state.controller.status.mode is Mode.INTERROGATING
        assert app.state.controller.status.desired_action is Action.FORWARDS


async def test_lifespan_stops_the_motor_on_shutdown():
    app = build_app(Settings())
    motor = app.state.motor

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/actions/forwards")
        assert motor.speed == 50

    assert motor.speed == 0
    assert motor.closed
    assert app.state.led.pattern is LedPattern.OFF


def test_build_power_returns_a_fake_when_hardware_disabled():
    """The dev server must never be able to power off a development laptop."""
    assert isinstance(build_power(Settings(use_real_hardware=False)), FakePower)


async def test_fake_power_records_the_request_instead_of_acting():
    power = FakePower()
    assert power.shutdown_called is False
    await power.shutdown()
    assert power.shutdown_called is True


async def test_lifespan_announces_readiness():
    """The robot says it is ready once everything is wired up."""
    spoken: list[str] = []

    async def record(text: str) -> None:
        spoken.append(text)

    app = build_app(Settings())
    app.state.speech.say = record

    async with app.router.lifespan_context(app):
        await app.state.announcement

    assert spoken == ["I am Verbot! How may I help you?"]


async def test_announcement_does_not_delay_serving():
    """Fire-and-forget: the app serves before the phrase finishes."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_say(text: str) -> None:
        started.set()
        await release.wait()

    app = build_app(Settings())
    app.state.speech.say = slow_say

    async with app.router.lifespan_context(app):
        await started.wait()
        assert not app.state.announcement.done()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/healthz")
        assert response.status_code == 200
        release.set()


async def test_empty_announcement_is_silent():
    async def fail_say(text: str) -> None:
        raise AssertionError("should not speak")

    app = build_app(Settings(startup_announcement=""))
    app.state.speech.say = fail_say

    async with app.router.lifespan_context(app):
        assert app.state.announcement is None


async def test_announcement_failure_does_not_break_startup():
    """A wedged sound card must not stop the robot from serving."""

    async def broken_say(text: str) -> None:
        raise RuntimeError("no sound card")

    app = build_app(Settings())
    app.state.speech.say = broken_say

    async with app.router.lifespan_context(app):
        await app.state.announcement
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/healthz")
        assert response.status_code == 200
