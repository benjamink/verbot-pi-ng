import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from verbot.actions import Action, Mode
from verbot.api import create_app
from verbot.config import Settings
from verbot.controller import Controller
from verbot.hardware.fakes import FakeMotor, FakePower, FakeSpeech, FakeSwitchBank


@pytest.fixture
async def client_rig():
    motor, switches, speech = FakeMotor(), FakeSwitchBank(), FakeSpeech()
    settings = Settings()
    controller = Controller(motor=motor, switches=switches, settings=settings)
    await controller.start()
    app = create_app(controller=controller, speech=speech, settings=settings, power=FakePower())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, controller, motor, switches, speech
    await controller.close()


@pytest.fixture
async def secure_rig():
    """An app with the shutdown endpoint switched on."""
    motor, switches, speech = FakeMotor(), FakeSwitchBank(), FakeSpeech()
    settings = Settings(shutdown_token="correct-horse-battery-staple")
    controller = Controller(motor=motor, switches=switches, settings=settings)
    await controller.start()
    power = FakePower()
    app = create_app(controller=controller, speech=speech, settings=settings, power=power)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, power, motor
    await controller.close()


@pytest.fixture
async def impatient_rig():
    """Like client_rig but the interrogation watchdog fires almost at once."""
    motor, switches, speech = FakeMotor(), FakeSwitchBank(), FakeSpeech()
    settings = Settings(interrogation_timeout_s=0.05)
    controller = Controller(motor=motor, switches=switches, settings=settings)
    await controller.start()
    app = create_app(controller=controller, speech=speech, settings=settings, power=FakePower())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, controller, motor, switches, speech
    await controller.close()


async def test_healthz(client_rig):
    client, *_ = client_rig
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_status_reports_idle_at_rest(client_rig):
    client, *_ = client_rig
    response = await client.get("/status")
    assert response.status_code == 200
    assert response.json() == {
        "mode": "idle",
        "current_action": None,
        "desired_action": None,
    }


async def test_posting_an_action_starts_interrogation(client_rig):
    client, controller, motor, _, _ = client_rig
    response = await client.post("/actions/forwards")
    assert response.status_code == 202
    assert response.json()["desired_action"] == "forwards"
    assert controller.status.mode is Mode.INTERROGATING
    assert motor.speed == 50


async def test_status_follows_the_state_machine(client_rig):
    client, _, _, switches, _ = client_rig
    await client.post("/actions/forwards")
    await switches.sweep_to(Action.FORWARDS)

    body = (await client.get("/status")).json()
    assert body == {
        "mode": "acting",
        "current_action": "forwards",
        "desired_action": None,
    }


async def test_unknown_action_is_rejected(client_rig):
    """The original project silently ignored bad actions and returned success."""
    client, controller, motor, _, _ = client_rig
    response = await client.post("/actions/dance")
    assert response.status_code == 422
    assert motor.speed == 0
    assert controller.status.mode is Mode.IDLE


async def test_stop_endpoint(client_rig):
    client, controller, _, _, _ = client_rig
    await client.post("/actions/forwards")
    response = await client.post("/stop")
    assert response.status_code == 202
    assert controller.status.desired_action is Action.STOP


async def test_say_endpoint(client_rig):
    client, _, _, _, speech = client_rig
    response = await client.post("/say", json={"text": "I am Verbot"})
    assert response.status_code == 202
    assert speech.spoken == ["I am Verbot"]


async def test_say_rejects_empty_text(client_rig):
    client, _, _, _, speech = client_rig
    response = await client.post("/say", json={"text": "   "})
    assert response.status_code == 422
    assert speech.spoken == []


async def test_shutdown_route_is_absent_without_a_token(client_rig):
    """Not 403 - absent. The capability should not even be advertised."""
    client, *_ = client_rig
    assert (await client.post("/system/shutdown")).status_code == 404

    schema = (await client.get("/openapi.json")).json()
    assert "/system/shutdown" not in schema["paths"]


@pytest.mark.parametrize("blank_token", ["", "   "])
async def test_shutdown_route_is_absent_with_a_blank_token(blank_token):
    """An empty or whitespace token must be treated as unset, not as a real
    credential that an empty header can satisfy."""
    motor, switches, speech = FakeMotor(), FakeSwitchBank(), FakeSpeech()
    settings = Settings(shutdown_token=blank_token)
    controller = Controller(motor=motor, switches=switches, settings=settings)
    await controller.start()
    power = FakePower()
    app = create_app(controller=controller, speech=speech, settings=settings, power=power)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/system/shutdown", headers={"X-Verbot-Token": blank_token})
        assert response.status_code == 404
        assert power.shutdown_called is False

        schema = (await client.get("/openapi.json")).json()
        assert "/system/shutdown" not in schema["paths"]
    await controller.close()


async def test_shutdown_rejects_a_missing_token(secure_rig):
    client, power, _ = secure_rig
    response = await client.post("/system/shutdown")
    assert response.status_code == 401
    assert power.shutdown_called is False


async def test_shutdown_rejects_a_wrong_token(secure_rig):
    client, power, _ = secure_rig
    response = await client.post("/system/shutdown", headers={"X-Verbot-Token": "wrong"})
    assert response.status_code == 401
    assert power.shutdown_called is False


async def test_shutdown_accepts_the_correct_token_and_stops_the_motor(secure_rig):
    client, power, motor = secure_rig
    await client.post("/actions/forwards")
    assert motor.speed != 0

    response = await client.post(
        "/system/shutdown", headers={"X-Verbot-Token": "correct-horse-battery-staple"}
    )

    assert response.status_code == 202
    assert response.json() == {"status": "shutting down"}
    assert power.shutdown_called is True
    assert motor.speed == 0


async def test_get_speeds_reports_the_current_settings(client_rig):
    client, *_ = client_rig
    response = await client.get("/speeds")
    assert response.status_code == 200
    assert response.json() == {
        "interrogation_speed": Settings().interrogation_speed,
        "action_speed": Settings().action_speed,
    }


async def test_patch_speeds_changes_what_the_motor_is_driven_at(client_rig):
    """The point of the sliders: the next action must use the new value."""
    client, controller, motor, *_ = client_rig

    response = await client.patch("/speeds", json={"interrogation_speed": 35})
    assert response.status_code == 200
    assert response.json()["interrogation_speed"] == 35

    await client.post("/actions/forwards")
    assert motor.speed == 35


async def test_patch_speeds_accepts_a_partial_body(client_rig):
    client, *_ = client_rig
    response = await client.patch("/speeds", json={"action_speed": -60})
    assert response.status_code == 200
    body = response.json()
    assert body["action_speed"] == -60
    assert body["interrogation_speed"] == Settings().interrogation_speed


@pytest.mark.parametrize(
    "payload",
    [
        {"interrogation_speed": 101},
        {"interrogation_speed": -1},
        {"action_speed": -101},
        {"action_speed": 101},
    ],
)
async def test_patch_speeds_rejects_out_of_range(client_rig, payload):
    """set_speed_percent would raise ValueError mid-action otherwise."""
    client, *_ = client_rig
    response = await client.patch("/speeds", json=payload)
    assert response.status_code == 422


async def test_index_serves_the_control_page(client_rig):
    client, *_ = client_rig
    response = await client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    # Every action must be reachable from the page.
    for action in Action:
        assert action.value in body
    # And the three things asked for beyond the buttons.
    assert 'id="say-text"' in body
    assert 'id="status-panel"' in body
    assert 'id="log"' in body


async def test_halt_cuts_the_motor_without_interrogating(client_rig):
    """The emergency control must not drive the drum looking for the stop cam."""
    client, controller, motor, *_ = client_rig

    await client.post("/actions/forwards")
    assert motor.speed == Settings().interrogation_speed

    response = await client.post("/halt")

    assert response.status_code == 202
    assert motor.speed == 0
    assert response.json()["mode"] == Mode.IDLE


async def test_stop_still_interrogates_for_the_stop_cam(client_rig):
    """/stop parks the mechanism; /halt is the one that just cuts power."""
    client, controller, motor, *_ = client_rig

    await client.post("/stop")

    assert motor.speed == Settings().interrogation_speed
    assert controller.status.mode is Mode.INTERROGATING


async def test_page_wires_the_big_button_to_halt_not_stop(client_rig):
    """A control labelled STOP must not start the motor."""
    client, *_ = client_rig
    body = (await client.get("/")).text

    assert "'/halt'" in body
    # and the gearbox stop position stays reachable as an ordinary action
    assert 'data-action="stop"' in body


async def test_say_without_animate_leaves_the_motor_alone(client_rig):
    """Bring-up step 6 tests the speaker without moving anything."""
    client, controller, motor, switches, speech = client_rig

    response = await client.post("/say", json={"text": "hello"})

    assert response.status_code == 202
    assert response.json() == {"spoken": "hello", "animated": False}
    assert motor.speed == 0
    assert controller.status.mode is Mode.IDLE


async def test_say_with_animate_runs_the_talk_action_then_parks(client_rig):
    """The mouth must be moving before the phrase starts, and stop after."""
    client, controller, motor, switches, speech = client_rig

    async def until_desired(action):
        """Bounded: a test must fail, not hang, if the state never arrives."""
        for _ in range(500):
            if controller.status.desired_action is action:
                return True
            await asyncio.sleep(0.002)
        return False

    async def engage_then_park():
        # Reach ACTING so the phrase is spoken with the mouth moving...
        if not await until_desired(Action.TALK):
            return
        await switches.activate(Action.TALK)
        # ...then let the trailing interrogation for the stop cam complete.
        if not await until_desired(Action.STOP):
            return
        await switches.activate(Action.STOP)

    helper = asyncio.create_task(engage_then_park())
    response = await client.post("/say", json={"text": "hello", "animate": True})
    await helper

    assert response.status_code == 202
    assert response.json() == {"spoken": "hello", "animated": True}
    assert speech.spoken == ["hello"]
    assert motor.speed == 0
    assert controller.status.mode is Mode.IDLE


async def test_say_with_animate_still_speaks_when_the_mouth_never_engages(impatient_rig):
    """No mechanism attached: a still mouth must not cost you the phrase."""
    client, controller, motor, _switches, speech = impatient_rig

    response = await client.post("/say", json={"text": "hello", "animate": True})

    assert response.status_code == 202
    assert response.json() == {"spoken": "hello", "animated": False}
    assert speech.spoken == ["hello"]
    assert controller.status.mode is Mode.FAULT


async def test_page_has_no_shutdown_control_when_the_route_is_absent(client_rig):
    """No token configured means no /system/shutdown route, so no dead button."""
    client, *_ = client_rig
    body = (await client.get("/")).text
    assert 'id="shutdown"' not in body


async def test_page_offers_shutdown_when_configured(secure_rig):
    client, *_ = secure_rig
    body = (await client.get("/")).text
    assert 'id="shutdown"' in body


async def test_page_never_embeds_the_shutdown_token(secure_rig):
    """The page is unauthenticated; serving the token would defeat the guard."""
    client, *_ = secure_rig
    body = (await client.get("/")).text
    assert "correct-horse-battery-staple" not in body


@pytest.mark.parametrize("blank_token", ["", "   "])
async def test_page_has_no_shutdown_control_with_a_blank_token(blank_token):
    """A blank token must not leave a button pointed at a route that 404s."""
    motor, switches, speech = FakeMotor(), FakeSwitchBank(), FakeSpeech()
    settings = Settings(shutdown_token=blank_token)
    controller = Controller(motor=motor, switches=switches, settings=settings)
    await controller.start()
    app = create_app(controller=controller, speech=speech, settings=settings, power=FakePower())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get("/")).text
        assert 'id="shutdown"' not in body
    await controller.close()
