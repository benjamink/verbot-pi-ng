import pytest
from httpx import ASGITransport, AsyncClient

from verbot.actions import Action, Mode
from verbot.api import create_app
from verbot.config import Settings
from verbot.controller import Controller
from verbot.hardware.fakes import FakeMotor, FakeSpeech, FakeSwitchBank


@pytest.fixture
async def client_rig():
    motor, switches, speech = FakeMotor(), FakeSwitchBank(), FakeSpeech()
    controller = Controller(motor=motor, switches=switches, settings=Settings())
    await controller.start()
    app = create_app(controller=controller, speech=speech)
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
