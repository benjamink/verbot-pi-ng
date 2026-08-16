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
