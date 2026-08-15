# Shutdown Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in, token-guarded `POST /system/shutdown` so the Pi can be powered down over HTTP without SSH and without the OnOff SHIM.

**Architecture:** A `SystemPower` protocol joins the existing `MotorDriver` / `SpeechEngine` / `Keypad` family, with a real adapter that shells out to `sudo -n poweroff` and a fake that records the call — so the suite keeps running off-Pi with no hardware and no root. The route is registered only when `shutdown_token` is configured, leaving today's API surface byte-identical for anyone who does not opt in.

**Tech Stack:** Python 3.13, FastAPI, pydantic-settings, pytest (asyncio auto mode), httpx `ASGITransport`. No new dependencies — `secrets` is stdlib and `BackgroundTasks` ships with FastAPI.

Spec: `docs/superpowers/specs/2026-08-15-shutdown-endpoint-design.md`

## Global Constraints

- **No new dependencies.** `pyproject.toml` must not gain a runtime or dev requirement.
- **The suite must keep running off-Pi**, with no hardware, no root, and no network. `uv run pytest -q` currently reports **81 passed**; it must still pass on a development laptop.
- **`build_power` returns `FakePower` whenever `settings.use_real_hardware` is false.** This is a safety property, not a convenience: without it, running the dev server on a laptop with a token configured would power off the laptop.
- **When `settings.shutdown_token` is `None` the route is not registered at all** — not registered-then-403. It must be absent from `/openapi.json`.
- **Token comparison uses `secrets.compare_digest`** on `bytes`, never `==`.
- TDD throughout: write the failing test, watch it fail, implement, watch it pass, commit.
- Ruff-clean under this repo's config: `select = ["E", "F", "I", "UP", "B"]`, `line-length = 100`. Verify with `uv run ruff check`.
- Match the surrounding code's voice. This codebase comments *why*, not *what*; docstrings explain the non-obvious. Do not add narration to self-evident lines.

## File Structure

| File | Responsibility |
|---|---|
| `src/verbot/hardware/protocols.py` | add `SystemPower` — the interface the API depends on |
| `src/verbot/hardware/system_power.py` | **new** — `SubprocessPower`, the only code that touches the OS |
| `src/verbot/hardware/fakes.py` | add `FakePower` |
| `src/verbot/main_support.py` | add `build_power(settings)` — real-vs-fake selection |
| `src/verbot/controller.py` | add `Controller.halt()` |
| `src/verbot/config.py` | add `shutdown_token` |
| `src/verbot/api.py` | widen `create_app`, register the route conditionally |
| `src/verbot/__main__.py` | build and inject `power` |
| `tests/test_main.py` | `build_power` selection tests |
| `tests/test_controller.py` | `halt()` tests |
| `tests/test_api.py` | endpoint tests, both fixtures |
| `docs/deployment.md` | sudoers rule, token generation, caveats |
| `README.md` | one line in Features |

---

### Task 1: SystemPower protocol, adapter, fake, and selection

**Files:**
- Modify: `src/verbot/hardware/protocols.py`
- Create: `src/verbot/hardware/system_power.py`
- Modify: `src/verbot/hardware/fakes.py`
- Modify: `src/verbot/main_support.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `Settings` from `verbot.config`
- Produces: `SystemPower` protocol with `async def shutdown(self) -> None`; `SubprocessPower()` (no constructor arguments); `FakePower()` exposing `shutdown_called: bool`; `build_power(settings: Settings) -> SystemPower`. Tasks 3 and 4 depend on all four names.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_main.py`. Add `FakePower` to the existing `verbot.hardware.fakes` import on line 6 and `build_power` to the existing `verbot.main_support` import on line 8.

```python
def test_build_power_returns_a_fake_when_hardware_disabled():
    """The dev server must never be able to power off a development laptop."""
    assert isinstance(build_power(Settings(use_real_hardware=False)), FakePower)


async def test_fake_power_records_the_request_instead_of_acting():
    power = FakePower()
    assert power.shutdown_called is False
    await power.shutdown()
    assert power.shutdown_called is True
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_main.py -k power -v`
Expected: collection error — `ImportError: cannot import name 'FakePower'`.

- [ ] **Step 3: Add the protocol**

Append to `src/verbot/hardware/protocols.py`:

```python
class SystemPower(Protocol):
    """Power control for the machine the software runs on, not the robot."""

    async def shutdown(self) -> None:
        """Ask the operating system to power down. Returns once the request
        is issued, which is well before the machine actually stops."""
```

- [ ] **Step 4: Write the adapter**

Create `src/verbot/hardware/system_power.py`:

```python
"""Powering down the Pi itself.

The unit runs unprivileged, so this needs one narrow sudoers grant - see
docs/deployment.md. `-n` matters: without it, a missing grant makes sudo wait
for a password that nothing will ever type, and the request hangs instead of
failing.
"""

import asyncio
import logging

log = logging.getLogger(__name__)

POWEROFF_COMMAND = ("sudo", "-n", "poweroff")


class SubprocessPower:
    async def shutdown(self) -> None:
        log.warning("shutdown requested - powering off")
        try:
            proc = await asyncio.create_subprocess_exec(
                *POWEROFF_COMMAND,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            log.error("sudo not found - cannot power off")
            return

        returncode = await proc.wait()
        if returncode != 0:
            log.error(
                "poweroff exited %d - check the sudoers grant in docs/deployment.md",
                returncode,
            )
```

- [ ] **Step 5: Write the fake**

Append to `src/verbot/hardware/fakes.py`, matching the style of `FakeSpeech` above it:

```python
class FakePower:
    def __init__(self) -> None:
        self.shutdown_called = False

    async def shutdown(self) -> None:
        self.shutdown_called = True
```

- [ ] **Step 6: Add the selection function**

Append to `src/verbot/main_support.py`:

```python
def build_power(settings: Settings) -> SystemPower:
    """Real power control on the Pi, a fake everywhere else.

    The fake is not just convenience: without this branch, a development
    machine running with a shutdown token configured would power itself off.
    """
    if not settings.use_real_hardware:
        from verbot.hardware.fakes import FakePower

        return FakePower()

    from verbot.hardware.system_power import SubprocessPower

    return SubprocessPower()
```

Add `SystemPower` to the existing `verbot.hardware.protocols` import on line 6 of that file.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_main.py -k power -v`
Expected: 2 passed.

- [ ] **Step 8: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check`
Expected: 83 passed, `All checks passed!`

- [ ] **Step 9: Commit**

```bash
git add src/verbot/hardware/protocols.py src/verbot/hardware/system_power.py \
        src/verbot/hardware/fakes.py src/verbot/main_support.py tests/test_main.py
git commit -m "feat: SystemPower protocol with a subprocess adapter and fake"
```

---

### Task 2: Controller.halt()

**Files:**
- Modify: `src/verbot/controller.py`
- Test: `tests/test_controller.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `Controller.halt() -> None` (async). Task 3 calls it.

`halt()` is **not** `Action.STOP`. `Action.STOP` interrogates for the stop cam and takes seconds of motor movement; `halt()` cuts the motor immediately without moving the robot at all.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_controller.py`. It already has a `rig` fixture (line 16) yielding `(controller, motor, switches)` with a `FakeMotor` and `FakeSwitchBank` already started — use it, and note the `settings` fixture sets `interrogation_timeout_s=0.05`.

```python
async def test_halt_stops_the_motor_without_moving_the_robot(rig):
    """Used on the way to a system shutdown, so it must not interrogate."""
    controller, motor, _ = rig
    await controller.request_action(Action.FORWARDS)
    assert motor.speed != 0

    await controller.halt()

    assert motor.speed == 0
    assert controller.status.mode is Mode.IDLE
    assert controller.status.desired_action is None


async def test_halt_leaves_no_watchdog_running(rig):
    """A timeout firing after halt would flip status to fault for no reason.

    The rig's interrogation_timeout_s is 0.05, so waiting past it is enough to
    catch a watchdog that halt failed to cancel.
    """
    controller, _, _ = rig
    await controller.request_action(Action.TALK)

    await controller.halt()
    await asyncio.sleep(0.1)

    assert controller.status.mode is Mode.IDLE
```

The second test deliberately asserts on observable status rather than the private `_timeout_task`: what matters is that no watchdog fires, not which attribute holds it.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_controller.py -k halt -v`
Expected: FAIL with `AttributeError: 'Controller' object has no attribute 'halt'`.

- [ ] **Step 3: Implement halt**

Add to `src/verbot/controller.py`, immediately after `close()`:

```python
    async def halt(self) -> None:
        """Cut the motor now, without running an action.

        Deliberately not Action.STOP: that interrogates for the stop cam and
        takes seconds of movement. This is for the moment before the machine
        powers off, when the robot should simply stop. `_current` is left
        alone - it still records the last cam the drum reached.
        """
        self._cancel_timeout()
        self._mode = Mode.IDLE
        self._desired = None
        await self._motor.set_speed_percent(0)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_controller.py -k halt -v`
Expected: 2 passed.

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check`
Expected: 85 passed, `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/verbot/controller.py tests/test_controller.py
git commit -m "feat: Controller.halt() stops the motor without interrogating"
```

---

### Task 3: The endpoint

**Files:**
- Modify: `src/verbot/config.py:21-23`
- Modify: `src/verbot/api.py`
- Modify: `src/verbot/__main__.py:18-49`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `SystemPower`, `FakePower`, `build_power` from Task 1; `Controller.halt()` from Task 2.
- Produces: `Settings.shutdown_token: str | None`; the widened signature
  `create_app(controller: Controller, speech: SpeechEngine, settings: Settings, power: SystemPower) -> FastAPI`.

Both new `create_app` parameters are **required**. There are only two call sites, and a default would let a caller silently get an app with no shutdown route when they meant to configure one.

- [ ] **Step 1: Add the setting**

In `src/verbot/config.py`, add to `Settings` in the front-panel/keypad area, with a comment:

```python
    # Shutdown endpoint. Unset means the route is never registered - see
    # docs/deployment.md before turning it on.
    shutdown_token: str | None = None
```

- [ ] **Step 2: Update the existing fixture and add a second one**

In `tests/test_api.py`, change the `client_rig` fixture's `create_app` call (line 16) to pass the two new arguments, and add a fixture for the token-enabled app. Add `FakePower` to the `verbot.hardware.fakes` import on line 9.

```python
@pytest.fixture
async def client_rig():
    motor, switches, speech = FakeMotor(), FakeSwitchBank(), FakeSpeech()
    settings = Settings()
    controller = Controller(motor=motor, switches=switches, settings=settings)
    await controller.start()
    app = create_app(
        controller=controller, speech=speech, settings=settings, power=FakePower()
    )
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
```

- [ ] **Step 3: Write the failing tests**

Append to `tests/test_api.py`:

```python
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
    response = await client.post(
        "/system/shutdown", headers={"X-Verbot-Token": "wrong"}
    )
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
```

- [ ] **Step 4: Run them to verify they fail**

Run: `uv run pytest tests/test_api.py -k shutdown -v`
Expected: `test_shutdown_route_is_absent_without_a_token` passes already (nothing is registered yet); the other three fail with 404 instead of 401/202.

- [ ] **Step 5: Widen create_app and register the route**

In `src/verbot/api.py`, add to the imports:

```python
import secrets

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, status

from verbot.config import Settings
from verbot.hardware.protocols import SpeechEngine, SystemPower
```

Change the signature and add the route immediately before `return app`:

```python
def create_app(
    controller: Controller,
    speech: SpeechEngine,
    settings: Settings,
    power: SystemPower,
) -> FastAPI:
```

```python
    if settings.shutdown_token is not None:
        expected = settings.shutdown_token.encode()

        @app.post("/system/shutdown", tags=["system"], status_code=status.HTTP_202_ACCEPTED)
        async def shutdown(
            background: BackgroundTasks,
            controller: ControllerDep,
            x_verbot_token: Annotated[str | None, Header()] = None,
        ) -> dict[str, str]:
            """Power the machine off. Requires the configured token.

            Registered only when a token is set, so the default deployment has
            no such route at all rather than a route that always refuses.
            """
            if x_verbot_token is None or not secrets.compare_digest(
                x_verbot_token.encode(), expected
            ):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="invalid or missing shutdown token",
                )

            # Stop the robot before the machine goes: systemd's teardown would
            # get there eventually, but not for a few hundred milliseconds, and
            # not at all if the poweroff itself fails.
            await controller.halt()
            # A background task runs after the response is sent, so the 202
            # reaches the caller rather than dying with the machine.
            background.add_task(power.shutdown)
            return {"status": "shutting down"}
```

Compare on `bytes`: `secrets.compare_digest` raises `TypeError` on `str` arguments containing non-ASCII, and a token is arbitrary operator input.

- [ ] **Step 6: Wire it up in `__main__.py`**

In `src/verbot/__main__.py`, add `build_power` to the existing `verbot.main_support` import, then in `build_app`:

```python
    power = build_power(settings)
```

next to the existing `build_hardware` / `build_keypad` calls, and change the `create_app` call to:

```python
    app = create_app(controller=controller, speech=speech, settings=settings, power=power)
```

Also expose it beside the other composed hardware, so tests and introspection can reach it:

```python
    app.state.power = power
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_api.py -k shutdown -v`
Expected: 4 passed.

- [ ] **Step 8: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check`
Expected: 89 passed, `All checks passed!`

- [ ] **Step 9: Commit**

```bash
git add src/verbot/config.py src/verbot/api.py src/verbot/__main__.py tests/test_api.py
git commit -m "feat: opt-in token-guarded POST /system/shutdown"
```

---

### Task 4: Document the deployment

**Files:**
- Modify: `docs/deployment.md`
- Modify: `README.md:24-32`

No code, no tests. The sudoers rule is the security-critical part of this feature, so it gets the same care as the code.

- [ ] **Step 1: Add a section to `docs/deployment.md`**

Insert after the "## 7. Service" section and before "## Bring-up checklist":

````markdown
## 8. Shutdown endpoint (optional)

Without the OnOff SHIM there is no power button, and pulling USB power without
a clean shutdown risks corrupting the SD card. This endpoint gives you a
shutdown over HTTP. It is off unless you configure a token.

Generate one and put it in the working directory's `.env`:

```bash
openssl rand -hex 32
```

```
VERBOT_SHUTDOWN_TOKEN=<the generated value>
```

The service runs unprivileged, so `poweroff` needs one narrow grant. Check the
real path first — it is `/usr/sbin` on Trixie and `/sbin` on older images:

```bash
command -v poweroff
```

```bash
sudo tee /etc/sudoers.d/verbot-poweroff <<'EOF'
verbot ALL=(root) NOPASSWD: /usr/sbin/poweroff
EOF
sudo chmod 0440 /etc/sudoers.d/verbot-poweroff
sudo visudo -c
```

Replace `verbot` with the user the service runs as, and the path with whatever
`command -v poweroff` reported. Scope it to that one binary — never
`NOPASSWD: ALL`. A wrong path fails closed, which at least is the safe
direction.

Then:

```bash
curl -X POST localhost:8080/system/shutdown -H 'X-Verbot-Token: <token>'
```

```json
{"status": "shutting down"}
```

Polkit is not an alternative here: a systemd service has no seat or login
session, so the usual "a local user may power off without sudo" path does not
apply.

**Two things worth knowing.** The token travels as a plaintext header over
unencrypted HTTP — acceptable on a home LAN, worth thinking about before
exposing the robot more widely. And the rest of the API has no authentication
at all while advertising itself over mDNS, so anything on the network can
already drive the robot; this endpoint is guarded separately because powering
the machine off is a different proposition from waving its arms.
````

Renumber nothing else — the existing sections stop at 7.

- [ ] **Step 2: Add a line to `README.md`**

In the `## Features` list, after the "**Status LED**" bullet:

```markdown
- **Optional shutdown endpoint** — power the Pi down over HTTP, guarded by a
  token and off by default
```

- [ ] **Step 3: Verify the docs are accurate**

Run: `uv run pytest -q && uv run ruff check`
Expected: 89 passed, `All checks passed!`

Then re-read the section you wrote against `src/verbot/api.py` and
`src/verbot/hardware/system_power.py`. Confirm the header name, the JSON
response body, and the command in `POWEROFF_COMMAND` all match what you
documented. A deployment doc that disagrees with the code costs more than no
doc at all.

- [ ] **Step 4: Commit**

```bash
git add docs/deployment.md README.md
git commit -m "docs: deploying the shutdown endpoint and its sudoers grant"
```

---

## Notes for the implementer

**On the sudoers path.** `/usr/sbin/poweroff` is correct for 64-bit Raspberry Pi OS (Trixie) but has not been verified on the target machine. The plan tells the operator to check with `command -v poweroff` rather than asserting it. Do not silently "fix" this to a path you have not checked either.

**On `sudo -n`.** The `-n` flag is load-bearing. Without it, a missing or wrong sudoers grant makes `sudo` block waiting for a password on a stdin nothing will ever write to, and the shutdown request hangs forever instead of failing and logging.

**On background tasks in tests.** Starlette runs `BackgroundTasks` after sending the response but still inside the ASGI call, and httpx's `ASGITransport` awaits that call to completion. So `power.shutdown_called` is already `True` by the time `await client.post(...)` returns — no polling or sleeping needed.

**Test counts.** The suite is at 81 before this plan. Expected totals: 83 after Task 1, 85 after Task 2, 89 after Task 3. If your numbers differ, find out why before continuing.
