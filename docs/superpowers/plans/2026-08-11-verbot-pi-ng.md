# verbot-pi-ng Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Control a Tomy Verbot toy robot from a Raspberry Pi Zero 2 W over an HTTP API, with speech output and a working front-panel keypad.

**Architecture:** A single `Controller` owns the interrogation/action state machine — the only piece carrying irreplaceable domain knowledge about the 1984 gearbox. All hardware sits behind `typing.Protocol` interfaces with in-memory fakes, so the entire state machine and API are tested on a dev machine with no Pi attached. Real hardware adapters (kernel PWM via sysfs, lgpio switch inputs, MCP23017 keypad) are thin, isolated, and imported only on the Pi.

**Tech Stack:** Python 3.13, uv, FastAPI + uvicorn, pydantic v2 / pydantic-settings, zeroconf, lgpio, smbus2, espeak-ng, pytest + pytest-asyncio.

## Global Constraints

- Target device: Raspberry Pi Zero 2 W, 64-bit Raspberry Pi OS (Trixie).
- `requires-python = ">=3.13"`. Already set in `pyproject.toml`.
- Hardware libraries (`lgpio`, `smbus2`) live in the `pi` optional-dependency extra and **must never be imported at module scope** in code reachable from tests. Import them inside the adapter modules under `src/verbot/hardware/`, which tests do not import.
- Every task must leave `uv run pytest` green and `uv run ruff check .` clean.
- Motor safety: any code path that leaves the state machine must guarantee the motor ends at speed 0. Never leave the motor running on an error path.
- Speeds are percentages in `[-100, 100]`. Positive = interrogation (anti-clockwise), negative = action (clockwise). This sign convention is load-bearing; do not change it.
- Switches are active-low with pull-ups. The `SwitchBank` protocol converts this to a domain-level `activated: bool` so the controller never reasons about voltage levels.
- Commit after every task.

## Reference

Read [`docs/hardware.md`](../../hardware.md) before Task 3. The interrogation/action mechanism is unintuitive and the state machine will not make sense without it.

## File Structure

| File | Responsibility |
|------|----------------|
| `src/verbot/actions.py` | `Action` and `Mode` enums, `ControllerStatus` model |
| `src/verbot/config.py` | `Settings` — pins, speeds, timeouts, env-overridable |
| `src/verbot/hardware/protocols.py` | `MotorDriver`, `SwitchBank`, `Keypad`, `StatusLed`, `SpeechEngine` |
| `src/verbot/hardware/fakes.py` | In-memory implementations for tests and dev |
| `src/verbot/hardware/pwm_motor.py` | Kernel sysfs PWM + lgpio direction pin |
| `src/verbot/hardware/lgpio_switches.py` | lgpio edge alerts marshalled onto the event loop |
| `src/verbot/hardware/mcp23017.py` | I2C keypad polling + status LED |
| `src/verbot/controller.py` | The interrogation/action state machine |
| `src/verbot/speech.py` | espeak-ng subprocess engine |
| `src/verbot/api.py` | FastAPI app and routes |
| `src/verbot/discovery.py` | Zeroconf service registration |
| `src/verbot/__main__.py` | Composition root — builds real or fake hardware, runs uvicorn |

---

### Task 1: Core types and settings

**Files:**
- Create: `src/verbot/actions.py`
- Create: `src/verbot/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Action` (str Enum, 8 members), `Mode` (str Enum: `IDLE`, `INTERROGATING`, `ACTING`, `FAULT`), `ControllerStatus` (pydantic model), `Settings` (pydantic-settings).

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
from verbot.actions import Action, ControllerStatus, Mode
from verbot.config import Settings


def test_every_action_has_a_switch_pin():
    settings = Settings()
    assert set(settings.switch_pins) == set(Action)


def test_switch_pins_are_unique():
    settings = Settings()
    pins = list(Settings().switch_pins.values())
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'verbot.actions'`

- [ ] **Step 3: Write the implementation**

Create `src/verbot/actions.py`:

```python
"""Domain vocabulary for the Verbot gearbox."""

from enum import Enum

from pydantic import BaseModel


class Action(str, Enum):
    """The eight gearbox positions, in interrogation drum order."""

    STOP = "stop"
    ROTATE_RIGHT = "rotate_right"
    ROTATE_LEFT = "rotate_left"
    FORWARDS = "forwards"
    REVERSE = "reverse"
    PUT_DOWN = "put_down"
    PICK_UP = "pick_up"
    TALK = "talk"


class Mode(str, Enum):
    """What the controller is currently doing."""

    IDLE = "idle"
    INTERROGATING = "interrogating"
    ACTING = "acting"
    FAULT = "fault"


class ControllerStatus(BaseModel):
    mode: Mode
    current_action: Action | None
    desired_action: Action | None
```

Create `src/verbot/config.py`:

```python
"""Runtime configuration. Every field is overridable via VERBOT_* env vars."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from verbot.actions import Action

# BCM pin per action. Unchanged from the original wiring - see docs/hardware.md.
DEFAULT_SWITCH_PINS: dict[Action, int] = {
    Action.STOP: 22,
    Action.ROTATE_RIGHT: 26,
    Action.ROTATE_LEFT: 10,
    Action.FORWARDS: 9,
    Action.REVERSE: 25,
    Action.PUT_DOWN: 11,
    Action.PICK_UP: 8,
    Action.TALK: 7,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VERBOT_", env_file=".env", extra="ignore")

    # HTTP server
    host: str = "0.0.0.0"
    port: int = 8080

    # Motor: kernel PWM on GPIO 12 (PWM0), direction on GPIO 5.
    pwm_chip: int = 0
    pwm_channel: int = 0
    pwm_period_ns: int = 4000  # 250 kHz, the DRV8835 maximum
    motor_dir_pin: int = 5
    interrogation_speed: int = 50
    action_speed: int = -100

    # Interrogation switch bank
    switch_pins: dict[Action, int] = Field(default_factory=lambda: dict(DEFAULT_SWITCH_PINS))
    switch_debounce_us: int = 25_000

    # Safety: give up if the expected switch never arrives.
    interrogation_timeout_s: float = 10.0

    # Speech
    speech_enabled: bool = True
    espeak_voice: str = "en-gb"
    espeak_pitch: int = 10
    espeak_speed: int = 130

    # Front-panel keypad (MCP23017)
    keypad_enabled: bool = True
    i2c_bus: int = 1
    mcp23017_address: int = 0x20
    keypad_poll_hz: float = 50.0
    keypad_debounce_samples: int = 2

    # Hardware toggle: False runs entirely on fakes (dev machines).
    use_real_hardware: bool = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: 5 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check . && uv run ruff format .
git add pyproject.toml uv.lock .gitignore .python-version LICENSE README.md docs config src tests
git commit -m "feat: project scaffolding, domain types and settings"
```

---

### Task 2: Hardware protocols and fakes

**Files:**
- Create: `src/verbot/hardware/__init__.py`
- Create: `src/verbot/hardware/protocols.py`
- Create: `src/verbot/hardware/fakes.py`
- Test: `tests/test_fakes.py`

**Interfaces:**
- Consumes: `Action` from Task 1.
- Produces:
  - `MotorDriver` protocol: `async set_speed_percent(percent: int) -> None`, `async close() -> None`
  - `SwitchBank` protocol: `set_listener(SwitchListener) -> None`, `async start() -> None`, `async close() -> None`
  - `SwitchListener = Callable[[Action, bool], Awaitable[None]]`
  - `Keypad` protocol: `set_listener(ButtonListener) -> None`, `async start()`, `async close()`
  - `ButtonListener = Callable[[Action], Awaitable[None]]`
  - `StatusLed` protocol: `async set_pattern(LedPattern) -> None`, `async close() -> None`
  - `LedPattern` enum: `OFF`, `SOLID`, `SLOW_BLINK`, `FAST_BLINK`
  - `SpeechEngine` protocol: `async say(text: str) -> None`, `async close() -> None`
  - `FakeMotor` with `.speed` and `.speed_history: list[int]`
  - `FakeSwitchBank` with `async activate(action)`, `async release(action)`, `async sweep_to(action)`
  - `FakeKeypad` with `async press(action)`; `FakeLed` with `.pattern`; `FakeSpeech` with `.spoken: list[str]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_fakes.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_fakes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'verbot.hardware'`

- [ ] **Step 3: Write the protocols**

Create `src/verbot/hardware/__init__.py`:

```python
"""Hardware abstraction. Real adapters import lgpio/smbus2; fakes do not."""
```

Create `src/verbot/hardware/protocols.py`:

```python
"""Protocols every hardware adapter implements.

The controller depends only on these, which is what lets the entire state
machine be tested with no Raspberry Pi attached.
"""

from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Protocol

from verbot.actions import Action

SwitchListener = Callable[[Action, bool], Awaitable[None]]
ButtonListener = Callable[[Action], Awaitable[None]]


class LedPattern(str, Enum):
    OFF = "off"
    SOLID = "solid"
    SLOW_BLINK = "slow_blink"
    FAST_BLINK = "fast_blink"


class MotorDriver(Protocol):
    async def set_speed_percent(self, percent: int) -> None:
        """Set speed as a percentage in [-100, 100]. Sign selects direction."""

    async def close(self) -> None: ...


class SwitchBank(Protocol):
    """The eight interrogation switches inside the gearbox.

    Adapters translate electrical level to `activated`: True means the switch
    is closed (the cam is holding it down), regardless of pull-up polarity.
    """

    def set_listener(self, listener: SwitchListener) -> None: ...
    async def start(self) -> None: ...
    async def close(self) -> None: ...


class Keypad(Protocol):
    """The eight red buttons on the original front panel."""

    def set_listener(self, listener: ButtonListener) -> None: ...
    async def start(self) -> None: ...
    async def close(self) -> None: ...


class StatusLed(Protocol):
    async def set_pattern(self, pattern: LedPattern) -> None: ...
    async def close(self) -> None: ...


class SpeechEngine(Protocol):
    async def say(self, text: str) -> None: ...
    async def close(self) -> None: ...
```

Create `src/verbot/hardware/fakes.py`:

```python
"""In-memory hardware. Used by the test suite and by `verbot` on a dev machine."""

from verbot.actions import Action
from verbot.hardware.protocols import (
    ButtonListener,
    LedPattern,
    SwitchListener,
)

# Physical order of the cams around the interrogation drum.
DRUM_ORDER: tuple[Action, ...] = (
    Action.STOP,
    Action.ROTATE_RIGHT,
    Action.ROTATE_LEFT,
    Action.FORWARDS,
    Action.REVERSE,
    Action.PUT_DOWN,
    Action.PICK_UP,
    Action.TALK,
)


class FakeMotor:
    def __init__(self) -> None:
        self.speed: int = 0
        self.speed_history: list[int] = []
        self.closed = False

    async def set_speed_percent(self, percent: int) -> None:
        if not -100 <= percent <= 100:
            raise ValueError(f"speed {percent} out of range [-100, 100]")
        self.speed = percent
        self.speed_history.append(percent)

    async def close(self) -> None:
        self.closed = True


class FakeSwitchBank:
    def __init__(self) -> None:
        self._listener: SwitchListener | None = None
        self.started = False
        self.closed = False

    def set_listener(self, listener: SwitchListener) -> None:
        self._listener = listener

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    async def _emit(self, action: Action, activated: bool) -> None:
        if self._listener is not None:
            await self._listener(action, activated)

    async def activate(self, action: Action) -> None:
        await self._emit(action, True)

    async def release(self, action: Action) -> None:
        await self._emit(action, False)

    async def sweep_to(self, target: Action) -> None:
        """Rotate the drum from the start of its cycle until `target` closes.

        Every switch before the target activates and releases in turn, exactly
        as the real cam drum does.
        """
        for action in DRUM_ORDER:
            await self.activate(action)
            if action is target:
                return
            await self.release(action)
        raise AssertionError(f"{target} is not on the drum")


class FakeKeypad:
    def __init__(self) -> None:
        self._listener: ButtonListener | None = None
        self.started = False
        self.closed = False

    def set_listener(self, listener: ButtonListener) -> None:
        self._listener = listener

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    async def press(self, action: Action) -> None:
        if self._listener is not None:
            await self._listener(action)


class FakeLed:
    def __init__(self) -> None:
        self.pattern = LedPattern.OFF
        self.closed = False

    async def set_pattern(self, pattern: LedPattern) -> None:
        self.pattern = pattern

    async def close(self) -> None:
        self.closed = True


class FakeSpeech:
    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.closed = False

    async def say(self, text: str) -> None:
        self.spoken.append(text)

    async def close(self) -> None:
        self.closed = True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_fakes.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/verbot/hardware tests/test_fakes.py
git commit -m "feat: hardware protocols and in-memory fakes"
```

---

### Task 3: Controller — interrogation to action

**Files:**
- Create: `src/verbot/controller.py`
- Test: `tests/test_controller.py`

**Interfaces:**
- Consumes: `Action`, `Mode`, `ControllerStatus`, `Settings`, `MotorDriver`, `SwitchBank`, `FakeMotor`, `FakeSwitchBank`.
- Produces: `Controller(motor, switches, settings, speech=None, led=None)` with `async start()`, `async close()`, `async request_action(action: Action) -> None`, `async handle_switch_event(action: Action, activated: bool) -> None`, and property `status -> ControllerStatus`.

**Read `docs/hardware.md` first.** The two-phase interrogation/action dance is the entire point of this class.

- [ ] **Step 1: Write the failing test**

Create `tests/test_controller.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_controller.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'verbot.controller'`

- [ ] **Step 3: Write the implementation**

Create `src/verbot/controller.py`:

```python
"""The Verbot interrogation/action state machine.

Verbot has one bi-directional motor. Running it anti-clockwise rotates a cam
drum that closes each of eight switches in turn (interrogation). Reversing to
clockwise stops the drum and drives the gear set at whichever switch is closed
(action). So performing action X means: interrogate until X closes, then
reverse. See docs/hardware.md.
"""

import asyncio
import logging

from verbot.actions import Action, ControllerStatus, Mode
from verbot.config import Settings
from verbot.hardware.protocols import MotorDriver, SwitchBank

log = logging.getLogger(__name__)


class Controller:
    def __init__(
        self,
        motor: MotorDriver,
        switches: SwitchBank,
        settings: Settings,
    ) -> None:
        self._motor = motor
        self._switches = switches
        self._settings = settings
        self._mode = Mode.IDLE
        self._current: Action | None = None
        self._desired: Action | None = None
        self._timeout_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._switches.set_listener(self.handle_switch_event)
        await self._switches.start()
        await self._motor.set_speed_percent(0)

    async def close(self) -> None:
        self._cancel_timeout()
        await self._motor.set_speed_percent(0)
        await self._motor.close()
        await self._switches.close()

    @property
    def status(self) -> ControllerStatus:
        return ControllerStatus(
            mode=self._mode,
            current_action=self._current,
            desired_action=self._desired,
        )

    async def request_action(self, action: Action) -> None:
        """Begin interrogating for `action`. Returns as soon as the motor starts."""
        if self._mode is Mode.ACTING and action is self._current:
            log.debug("%s already running, ignoring request", action)
            return
        if self._mode is Mode.IDLE and action is Action.STOP:
            log.debug("already stopped, ignoring request")
            return

        log.info("interrogating for %s", action)
        self._desired = action
        self._mode = Mode.INTERROGATING
        self._start_timeout()
        await self._motor.set_speed_percent(self._settings.interrogation_speed)

    async def handle_switch_event(self, action: Action, activated: bool) -> None:
        """Called by the switch bank whenever a gearbox switch opens or closes."""
        if self._mode is Mode.INTERROGATING and activated and action is self._desired:
            await self._enter_action(action)

    async def _enter_action(self, action: Action) -> None:
        self._cancel_timeout()
        self._current = action
        self._desired = None

        if action is Action.STOP:
            log.info("reached stop position")
            self._mode = Mode.IDLE
            await self._motor.set_speed_percent(0)
        else:
            log.info("gears in position, performing %s", action)
            self._mode = Mode.ACTING
            await self._motor.set_speed_percent(self._settings.action_speed)

    def _start_timeout(self) -> None:
        self._cancel_timeout()
        self._timeout_task = asyncio.create_task(self._interrogation_timeout())

    def _cancel_timeout(self) -> None:
        if self._timeout_task is not None:
            self._timeout_task.cancel()
            self._timeout_task = None

    async def _interrogation_timeout(self) -> None:
        await asyncio.sleep(self._settings.interrogation_timeout_s)
        log.error("interrogation timed out waiting for %s - stopping motor", self._desired)
        self._mode = Mode.FAULT
        self._desired = None
        await self._motor.set_speed_percent(0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_controller.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/verbot/controller.py tests/test_controller.py
git commit -m "feat: interrogation/action state machine"
```

---

### Task 4: Controller — limit switches and the timeout watchdog

**Files:**
- Modify: `src/verbot/controller.py`
- Modify: `tests/test_controller.py`

**Interfaces:**
- Consumes: everything from Task 3.
- Produces: no new public API. `handle_switch_event` gains limit-switch handling; `Mode.FAULT` becomes reachable and `request_action` clears it.

**Context:** the arm actions have mechanical limit switches wired in series with the interrogation switch. When an arm hits its travel stop the circuit breaks, so the switch for the *currently running* action reports `activated=False`. That is the only signal that an action has finished, and ignoring it strains the mechanism.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_controller.py`:

```python
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
    import asyncio

    controller, motor, _ = rig
    await controller.request_action(Action.TALK)
    assert motor.speed == 50

    await asyncio.sleep(0.1)  # settings.interrogation_timeout_s is 0.05

    assert controller.status.mode is Mode.FAULT
    assert controller.status.desired_action is None
    assert motor.speed == 0


async def test_reaching_the_switch_cancels_the_timeout(rig):
    import asyncio

    controller, motor, switches = rig
    await controller.request_action(Action.FORWARDS)
    await switches.sweep_to(Action.FORWARDS)

    await asyncio.sleep(0.1)

    assert controller.status.mode is Mode.ACTING
    assert motor.speed == -100


async def test_a_new_request_clears_a_fault(rig):
    import asyncio

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_controller.py -v`
Expected: `test_limit_switch_stops_a_running_action` FAILS — mode is still `ACTING` because releases are ignored. The timeout tests should already pass from Task 3; confirm they do.

- [ ] **Step 3: Add limit-switch handling**

In `src/verbot/controller.py`, replace `handle_switch_event` with:

```python
    async def handle_switch_event(self, action: Action, activated: bool) -> None:
        """Called by the switch bank whenever a gearbox switch opens or closes.

        Two events matter, and mode disambiguates them:

        * INTERROGATING + closed + it is the one we want -> gears are in
          position, start the action.
        * ACTING + opened + it is the action we are running -> a mechanical
          limit switch broke the circuit, so the action is finished.

        Everything else is the drum sweeping past, and is ignored.
        """
        if self._mode is Mode.INTERROGATING and activated and action is self._desired:
            await self._enter_action(action)
        elif self._mode is Mode.ACTING and not activated and action is self._current:
            log.info("limit switch reached for %s", action)
            await self.request_action(Action.STOP)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_controller.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/verbot/controller.py tests/test_controller.py
git commit -m "feat: limit switch handling and interrogation watchdog"
```

---

### Task 5: Speech output

**Files:**
- Create: `src/verbot/speech.py`
- Test: `tests/test_speech.py`

**Interfaces:**
- Consumes: `Settings`, `SpeechEngine` protocol.
- Produces: `EspeakEngine(settings: Settings)` implementing `SpeechEngine`, plus `build_command(settings, text) -> list[str]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_speech.py`:

```python
import asyncio

from verbot.config import Settings
from verbot.speech import EspeakEngine, build_command


def test_build_command_uses_configured_voice():
    settings = Settings(espeak_voice="en-us", espeak_pitch=20, espeak_speed=150)
    cmd = build_command(settings, "hello")
    assert cmd == ["espeak-ng", "-v", "en-us", "-p", "20", "-s", "150", "--", "hello"]


def test_build_command_terminates_options_before_text():
    """Without `--`, text starting with a dash is parsed as a flag."""
    cmd = build_command(Settings(), "-v is not a flag here")
    assert cmd[-2] == "--"
    assert cmd[-1] == "-v is not a flag here"


async def test_say_runs_the_command(monkeypatch):
    calls: list[list[str]] = []

    class DummyProc:
        returncode = 0

        async def wait(self):
            return 0

        async def communicate(self):
            return (b"", b"")

        def kill(self):
            pass

    async def fake_exec(*args, **kwargs):
        calls.append(list(args))
        return DummyProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    engine = EspeakEngine(Settings())
    await engine.say("hello robot")

    assert calls == [["espeak-ng", "-v", "en-gb", "-p", "10", "-s", "130", "--", "hello robot"]]


async def test_say_is_a_no_op_when_speech_disabled(monkeypatch):
    async def fail_exec(*args, **kwargs):
        raise AssertionError("should not spawn a process")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_exec)

    engine = EspeakEngine(Settings(speech_enabled=False))
    await engine.say("silence")


async def test_say_serialises_overlapping_calls(monkeypatch):
    """Two concurrent says must not garble each other on one sound card."""
    running = 0
    max_concurrent = 0

    class DummyProc:
        returncode = 0

        async def wait(self):
            nonlocal running, max_concurrent
            running += 1
            max_concurrent = max(max_concurrent, running)
            await asyncio.sleep(0.01)
            running -= 1
            return 0

        def kill(self):
            pass

    async def fake_exec(*args, **kwargs):
        return DummyProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    engine = EspeakEngine(Settings())
    await asyncio.gather(engine.say("one"), engine.say("two"))

    assert max_concurrent == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_speech.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'verbot.speech'`

- [ ] **Step 3: Write the implementation**

Create `src/verbot/speech.py`:

```python
"""Speech output via espeak-ng.

espeak-ng is deliberate: it is tiny, has no model files, runs comfortably on a
Zero 2 W, and its clipped synthetic voice suits a 1984 toy robot far better
than a neural TTS would.
"""

import asyncio
import logging

from verbot.config import Settings

log = logging.getLogger(__name__)


def build_command(settings: Settings, text: str) -> list[str]:
    return [
        "espeak-ng",
        "-v",
        settings.espeak_voice,
        "-p",
        str(settings.espeak_pitch),
        "-s",
        str(settings.espeak_speed),
        "--",  # everything after this is text, never a flag
        text,
    ]


class EspeakEngine:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = asyncio.Lock()
        self._proc: asyncio.subprocess.Process | None = None

    async def say(self, text: str) -> None:
        if not self._settings.speech_enabled:
            log.debug("speech disabled, not saying %r", text)
            return

        # One sound card, one voice at a time.
        async with self._lock:
            cmd = build_command(self._settings, text)
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except FileNotFoundError:
                log.error("espeak-ng not installed - run: sudo apt install espeak-ng")
                return

            try:
                await self._proc.wait()
            finally:
                self._proc = None

    async def close(self) -> None:
        proc = self._proc
        if proc is not None and proc.returncode is None:
            proc.kill()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_speech.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/verbot/speech.py tests/test_speech.py
git commit -m "feat: espeak-ng speech output"
```

---

### Task 6: HTTP API

**Files:**
- Create: `src/verbot/api.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `Controller`, `SpeechEngine`, `Action`, `ControllerStatus`.
- Produces: `create_app(controller: Controller, speech: SpeechEngine) -> FastAPI` with routes `GET /healthz`, `GET /status`, `POST /actions/{action}`, `POST /stop`, `POST /say`. Exposes `app.state.controller` and `app.state.speech`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'verbot.api'`

- [ ] **Step 3: Write the implementation**

Create `src/verbot/api.py`:

```python
"""HTTP control surface.

Typing the path parameter as `Action` gets validation for free: an unknown
action is a 422 rather than a silently ignored request.
"""

from typing import Annotated

from fastapi import Depends, FastAPI, Request, status
from pydantic import BaseModel, Field, field_validator

from verbot.actions import Action, ControllerStatus
from verbot.controller import Controller
from verbot.hardware.protocols import SpeechEngine


class SayRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)

    @field_validator("text")
    @classmethod
    def not_only_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


def get_controller(request: Request) -> Controller:
    return request.app.state.controller


def get_speech(request: Request) -> SpeechEngine:
    return request.app.state.speech


ControllerDep = Annotated[Controller, Depends(get_controller)]
SpeechDep = Annotated[SpeechEngine, Depends(get_speech)]


def create_app(controller: Controller, speech: SpeechEngine) -> FastAPI:
    app = FastAPI(
        title="Verbot",
        description="Control a 1984 Tomy Verbot toy robot.",
        version="0.1.0",
    )
    app.state.controller = controller
    app.state.speech = speech

    @app.get("/healthz", tags=["system"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/status", tags=["control"], response_model=ControllerStatus)
    async def get_status(controller: ControllerDep) -> ControllerStatus:
        return controller.status

    @app.post(
        "/actions/{action}",
        tags=["control"],
        status_code=status.HTTP_202_ACCEPTED,
        response_model=ControllerStatus,
    )
    async def perform_action(action: Action, controller: ControllerDep) -> ControllerStatus:
        """Begin interrogating for `action`. Returns once the motor starts,
        not once the action completes."""
        await controller.request_action(action)
        return controller.status

    @app.post(
        "/stop",
        tags=["control"],
        status_code=status.HTTP_202_ACCEPTED,
        response_model=ControllerStatus,
    )
    async def stop(controller: ControllerDep) -> ControllerStatus:
        await controller.request_action(Action.STOP)
        return controller.status

    @app.post("/say", tags=["speech"], status_code=status.HTTP_202_ACCEPTED)
    async def say(body: SayRequest, speech: SpeechDep) -> dict[str, str]:
        await speech.say(body.text)
        return {"spoken": body.text}

    return app
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_api.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/verbot/api.py tests/test_api.py
git commit -m "feat: FastAPI control surface"
```

---

### Task 7: Composition root and mDNS discovery

**Files:**
- Create: `src/verbot/discovery.py`
- Create: `src/verbot/__main__.py`
- Test: `tests/test_discovery.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `Settings`, `Controller`, `EspeakEngine`, fakes, `create_app`.
- Produces: `service_info(settings, hostname, address) -> ServiceInfo`; `ServiceAdvertiser(settings)` with `async start()` / `async close()`; `build_hardware(settings) -> tuple[MotorDriver, SwitchBank]`; `main() -> None`.

**Note:** the original project's zeroconf registration passed a raw IP where mDNS expects a hostname, and could raise `UnboundLocalError` on the error path. Both are fixed here.

- [ ] **Step 1: Write the failing test**

Create `tests/test_discovery.py`:

```python
import socket

from verbot.config import Settings
from verbot.discovery import service_info


def test_service_info_uses_a_dotted_local_hostname():
    """mDNS requires a hostname ending in `.local.`, not an IP address."""
    info = service_info(Settings(port=8080), hostname="verbot", address="192.168.1.50")
    assert info.server == "verbot.local."
    assert info.port == 8080
    assert info.type == "_verbot._tcp.local."


def test_service_info_encodes_the_address():
    info = service_info(Settings(), hostname="verbot", address="192.168.1.50")
    assert info.addresses == [socket.inet_aton("192.168.1.50")]


def test_service_info_advertises_the_api_path():
    info = service_info(Settings(), hostname="verbot", address="192.168.1.50")
    assert info.properties[b"path"] == b"/status"
```

Create `tests/test_main.py`:

```python
from verbot.config import Settings
from verbot.hardware.fakes import FakeMotor, FakeSwitchBank
from verbot.main_support import build_hardware


def test_build_hardware_returns_fakes_when_hardware_disabled():
    motor, switches = build_hardware(Settings(use_real_hardware=False))
    assert isinstance(motor, FakeMotor)
    assert isinstance(switches, FakeSwitchBank)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_discovery.py tests/test_main.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'verbot.discovery'`

- [ ] **Step 3: Write the implementation**

Create `src/verbot/discovery.py`:

```python
"""Advertise the control server over mDNS so clients can find it by name."""

import logging
import socket

from zeroconf import IPVersion, ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

from verbot.config import Settings

log = logging.getLogger(__name__)

SERVICE_TYPE = "_verbot._tcp.local."


def local_address() -> str:
    """Best-effort primary IPv4 address, without needing a reachable target."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def service_info(settings: Settings, hostname: str, address: str) -> ServiceInfo:
    return ServiceInfo(
        SERVICE_TYPE,
        f"Verbot._verbot._tcp.local.",
        addresses=[socket.inet_aton(address)],
        port=settings.port,
        properties={"path": "/status"},
        server=f"{hostname}.local.",
    )


class ServiceAdvertiser:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._zc: AsyncZeroconf | None = None
        self._info: ServiceInfo | None = None

    async def start(self) -> None:
        try:
            self._info = service_info(
                self._settings,
                hostname=socket.gethostname().split(".")[0],
                address=local_address(),
            )
            self._zc = AsyncZeroconf(ip_version=IPVersion.V4Only)
            await self._zc.async_register_service(self._info)
            log.info("advertised %s on port %d", self._info.server, self._settings.port)
        except OSError as exc:
            # Discovery is a convenience; never let it stop the robot working.
            log.warning("mDNS registration failed: %s", exc)
            await self.close()

    async def close(self) -> None:
        if self._zc is not None:
            if self._info is not None:
                await self._zc.async_unregister_service(self._info)
            await self._zc.async_close()
        self._zc = None
        self._info = None
```

Create `src/verbot/main_support.py`:

```python
"""Hardware selection, split out from __main__ so it is importable in tests."""

import logging

from verbot.config import Settings
from verbot.hardware.protocols import MotorDriver, SwitchBank

log = logging.getLogger(__name__)


def build_hardware(settings: Settings) -> tuple[MotorDriver, SwitchBank]:
    """Return real adapters on the Pi, fakes everywhere else.

    The lgpio imports live inside this branch so dev machines never need them.
    """
    if not settings.use_real_hardware:
        from verbot.hardware.fakes import FakeMotor, FakeSwitchBank

        log.warning("VERBOT_USE_REAL_HARDWARE is false - running on fake hardware")
        return FakeMotor(), FakeSwitchBank()

    from verbot.hardware.lgpio_switches import LgpioSwitchBank
    from verbot.hardware.pwm_motor import KernelPwmMotor

    return KernelPwmMotor(settings), LgpioSwitchBank(settings)
```

Create `src/verbot/__main__.py`:

```python
"""Entrypoint. Wires hardware, controller, API and mDNS together."""

import asyncio
import contextlib
import logging

import uvicorn

from verbot.api import create_app
from verbot.config import Settings
from verbot.controller import Controller
from verbot.discovery import ServiceAdvertiser
from verbot.main_support import build_hardware
from verbot.speech import EspeakEngine


def build_app(settings: Settings):
    motor, switches = build_hardware(settings)
    controller = Controller(motor=motor, switches=switches, settings=settings)
    speech = EspeakEngine(settings)
    advertiser = ServiceAdvertiser(settings)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        await controller.start()
        await advertiser.start()
        try:
            yield
        finally:
            # Order matters: stop advertising, then guarantee the motor is off.
            await advertiser.close()
            await controller.close()
            await speech.close()

    app = create_app(controller=controller, speech=speech)
    app.router.lifespan_context = lifespan
    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    settings = Settings()
    uvicorn.run(build_app(settings), host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
```

Update `tests/test_main.py` to import from `verbot.main_support` (already written that way in Step 1).

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -v`
Expected: all tests pass (4 discovery/main + 30 earlier)

Then smoke-test the server on fakes:

```bash
uv run verbot &
sleep 3
curl -s localhost:8080/healthz
curl -s -X POST localhost:8080/actions/forwards
curl -s localhost:8080/status
kill %1
```

Expected: `{"status":"ok"}`, then a 202 body with `"desired_action":"forwards"`, then a status showing `"mode":"interrogating"`. Visit `http://localhost:8080/docs` to confirm the OpenAPI page renders.

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/verbot/discovery.py src/verbot/main_support.py src/verbot/__main__.py tests/
git commit -m "feat: entrypoint, lifespan wiring and mDNS advertisement"
```

---

### Task 8: Kernel PWM motor driver

**Files:**
- Create: `src/verbot/hardware/pwm_motor.py`
- Test: `tests/test_pwm_motor.py`

**Interfaces:**
- Consumes: `Settings`, `MotorDriver` protocol.
- Produces: `KernelPwmMotor(settings, sysfs_root: Path = Path("/sys/class/pwm"))` implementing `MotorDriver`.

**Context:** the motor's PWM is driven by the kernel through sysfs, not pigpio. That is what allows the I2S DAC to own the PCM peripheral while the motor still gets true 250 kHz hardware PWM. Direction is a plain GPIO output via lgpio.

Because sysfs is just files, the whole driver is testable off-Pi by pointing `sysfs_root` at a `tmp_path`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pwm_motor.py`:

```python
import pytest

from verbot.config import Settings
from verbot.hardware.pwm_motor import KernelPwmMotor


@pytest.fixture
def sysfs(tmp_path):
    """A minimal fake of /sys/class/pwm with one chip and one channel."""
    chip = tmp_path / "pwmchip0"
    channel = chip / "pwm0"
    channel.mkdir(parents=True)
    (chip / "export").write_text("")
    (chip / "unexport").write_text("")
    for name in ("period", "duty_cycle", "enable", "polarity"):
        (channel / name).write_text("0")
    return tmp_path


@pytest.fixture
def motor(sysfs):
    return KernelPwmMotor(Settings(), sysfs_root=sysfs, gpio=_FakeGpio())


class _FakeGpio:
    def __init__(self):
        self.values: list[int] = []
        self.closed = False

    def write(self, pin: int, value: int) -> None:
        self.values.append(value)

    def close(self) -> None:
        self.closed = True


def read(sysfs, name: str) -> str:
    return (sysfs / "pwmchip0" / "pwm0" / name).read_text().strip()


async def test_open_sets_period_and_enables(motor, sysfs):
    await motor.open()
    assert read(sysfs, "period") == "4000"  # 250 kHz
    assert read(sysfs, "enable") == "1"
    assert read(sysfs, "duty_cycle") == "0"


async def test_full_forward_is_full_duty(motor, sysfs):
    await motor.open()
    await motor.set_speed_percent(100)
    assert read(sysfs, "duty_cycle") == "4000"


async def test_half_speed_is_half_duty(motor, sysfs):
    await motor.open()
    await motor.set_speed_percent(50)
    assert read(sysfs, "duty_cycle") == "2000"


async def test_negative_speed_sets_direction_and_positive_duty(motor, sysfs):
    await motor.open()
    await motor.set_speed_percent(-100)
    assert read(sysfs, "duty_cycle") == "4000"
    assert motor._gpio.values[-1] == 1


async def test_positive_speed_clears_direction(motor, sysfs):
    await motor.open()
    await motor.set_speed_percent(50)
    assert motor._gpio.values[-1] == 0


async def test_out_of_range_is_rejected(motor):
    await motor.open()
    with pytest.raises(ValueError):
        await motor.set_speed_percent(-101)


async def test_close_zeroes_duty_before_disabling(motor, sysfs):
    """Leaving a duty cycle latched while disabling can twitch the motor."""
    await motor.open()
    await motor.set_speed_percent(100)
    await motor.close()
    assert read(sysfs, "duty_cycle") == "0"
    assert read(sysfs, "enable") == "0"
    assert motor._gpio.closed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pwm_motor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'verbot.hardware.pwm_motor'`

- [ ] **Step 3: Write the implementation**

Create `src/verbot/hardware/pwm_motor.py`:

```python
"""Motor driver: kernel hardware PWM (sysfs) + a GPIO direction pin.

Requires `dtoverlay=pwm,pin=12,func=4` in /boot/firmware/config.txt, which
exposes /sys/class/pwm/pwmchip0. Kernel PWM is used instead of pigpio because
pigpio needs the PCM peripheral for DMA timing in order to leave hardware PWM
free - and the I2S DAC needs PCM. The kernel PWM driver has no such conflict.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

from verbot.config import Settings

log = logging.getLogger(__name__)

DEFAULT_SYSFS_ROOT = Path("/sys/class/pwm")


class KernelPwmMotor:
    def __init__(
        self,
        settings: Settings,
        sysfs_root: Path = DEFAULT_SYSFS_ROOT,
        gpio: Any | None = None,
    ) -> None:
        self._settings = settings
        self._chip = sysfs_root / f"pwmchip{settings.pwm_chip}"
        self._channel = self._chip / f"pwm{settings.pwm_channel}"
        self._gpio = gpio
        self._owns_gpio = gpio is None

    async def open(self) -> None:
        if not self._channel.exists():
            self._write(self._chip / "export", str(self._settings.pwm_channel))
            # The kernel creates the channel directory asynchronously.
            for _ in range(50):
                if self._channel.exists():
                    break
                await asyncio.sleep(0.01)
            else:
                raise RuntimeError(f"PWM channel {self._channel} did not appear after export")

        self._write(self._channel / "duty_cycle", "0")
        self._write(self._channel / "period", str(self._settings.pwm_period_ns))
        self._write(self._channel / "enable", "1")

        if self._gpio is None:
            import lgpio

            handle = lgpio.gpiochip_open(0)
            lgpio.gpio_claim_output(handle, self._settings.motor_dir_pin, 0)
            self._gpio = _LgpioOutput(handle)

        log.info("motor ready: %s at %d ns period", self._channel, self._settings.pwm_period_ns)

    async def set_speed_percent(self, percent: int) -> None:
        if not -100 <= percent <= 100:
            raise ValueError(f"speed {percent} out of range [-100, 100]")

        direction = 1 if percent < 0 else 0
        magnitude = abs(percent)
        duty_ns = self._settings.pwm_period_ns * magnitude // 100

        # Direction first: changing it under load is harder on the H-bridge.
        self._gpio.write(self._settings.motor_dir_pin, direction)
        self._write(self._channel / "duty_cycle", str(duty_ns))

    async def close(self) -> None:
        try:
            self._write(self._channel / "duty_cycle", "0")
            self._write(self._channel / "enable", "0")
        except OSError as exc:
            log.warning("could not cleanly stop PWM: %s", exc)
        if self._gpio is not None and self._owns_gpio:
            self._gpio.close()
        elif self._gpio is not None:
            self._gpio.close()

    @staticmethod
    def _write(path: Path, value: str) -> None:
        path.write_text(value)


class _LgpioOutput:
    def __init__(self, handle: int) -> None:
        self._handle = handle

    def write(self, pin: int, value: int) -> None:
        import lgpio

        lgpio.gpio_write(self._handle, pin, value)

    def close(self) -> None:
        import lgpio

        lgpio.gpiochip_close(self._handle)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pwm_motor.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/verbot/hardware/pwm_motor.py tests/test_pwm_motor.py
git commit -m "feat: kernel PWM motor driver"
```

---

### Task 9: lgpio switch bank

**Files:**
- Create: `src/verbot/hardware/lgpio_switches.py`
- Test: `tests/test_lgpio_switches.py`

**Interfaces:**
- Consumes: `Settings`, `Action`, `SwitchBank` protocol.
- Produces: `LgpioSwitchBank(settings)` implementing `SwitchBank`, plus a pure helper `switch_event(pin_to_action, pin, level) -> tuple[Action, bool] | None` that is unit-testable without hardware.

**Context:** lgpio delivers edge alerts on its own thread, so events must be marshalled onto the asyncio loop. Keep this module thin — only the pure translation helper is tested off-Pi; the rest is verified during on-Pi bring-up in Task 11.

lgpio levels: 0 = low. Switches are pull-up and close to ground, so **level 0 means activated**.

- [ ] **Step 1: Write the failing test**

Create `tests/test_lgpio_switches.py`:

```python
from verbot.actions import Action
from verbot.config import DEFAULT_SWITCH_PINS
from verbot.hardware.lgpio_switches import PIN_TO_ACTION_DOC, switch_event

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
    assert PIN_TO_ACTION_DOC  # module documents the polarity decision
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_lgpio_switches.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'verbot.hardware.lgpio_switches'`

- [ ] **Step 3: Write the implementation**

Create `src/verbot/hardware/lgpio_switches.py`:

```python
"""Interrogation switch bank via lgpio.

lgpio calls back on its own thread, so every event is handed to the asyncio
loop with `call_soon_threadsafe`. The controller therefore only ever runs on
the loop and needs no locking.
"""

import asyncio
import logging

from verbot.actions import Action
from verbot.config import Settings
from verbot.hardware.protocols import SwitchListener

log = logging.getLogger(__name__)

PIN_TO_ACTION_DOC = (
    "Switch pins are inputs with pull-ups. A cam closing the switch pulls the "
    "pin to ground, so level 0 means activated."
)

LEVEL_LOW = 0
LEVEL_HIGH = 1
LEVEL_WATCHDOG = 2


def switch_event(
    pin_to_action: dict[int, Action], pin: int, level: int
) -> tuple[Action, bool] | None:
    """Translate an lgpio alert into a domain event, or None to ignore it."""
    action = pin_to_action.get(pin)
    if action is None:
        return None
    if level == LEVEL_LOW:
        return (action, True)
    if level == LEVEL_HIGH:
        return (action, False)
    return None  # watchdog timeout, not an edge


class LgpioSwitchBank:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pin_to_action = {pin: action for action, pin in settings.switch_pins.items()}
        self._listener: SwitchListener | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._handle: int | None = None
        self._callbacks: list[object] = []

    def set_listener(self, listener: SwitchListener) -> None:
        self._listener = listener

    async def start(self) -> None:
        import lgpio

        self._loop = asyncio.get_running_loop()
        self._handle = lgpio.gpiochip_open(0)

        for pin in self._pin_to_action:
            lgpio.gpio_claim_alert(self._handle, pin, lgpio.BOTH_EDGES, lgpio.SET_PULL_UP)
            lgpio.gpio_set_debounce_micros(self._handle, pin, self._settings.switch_debounce_us)
            self._callbacks.append(
                lgpio.callback(self._handle, pin, lgpio.BOTH_EDGES, self._on_alert)
            )

        log.info("watching %d interrogation switches", len(self._pin_to_action))

    def _on_alert(self, chip: int, pin: int, level: int, timestamp: int) -> None:
        """Runs on lgpio's thread. Do nothing here but hand off to the loop."""
        event = switch_event(self._pin_to_action, pin, level)
        if event is None or self._listener is None or self._loop is None:
            return
        action, activated = event
        self._loop.call_soon_threadsafe(self._dispatch, action, activated)

    def _dispatch(self, action: Action, activated: bool) -> None:
        if self._listener is not None:
            asyncio.create_task(self._listener(action, activated))

    async def close(self) -> None:
        import lgpio

        for cb in self._callbacks:
            cb.cancel()
        self._callbacks.clear()
        if self._handle is not None:
            lgpio.gpiochip_close(self._handle)
            self._handle = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_lgpio_switches.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/verbot/hardware/lgpio_switches.py tests/test_lgpio_switches.py
git commit -m "feat: lgpio interrogation switch bank"
```

---

### Task 10: Front-panel keypad and status LED

**Files:**
- Create: `src/verbot/hardware/mcp23017.py`
- Modify: `src/verbot/main_support.py`
- Modify: `src/verbot/__main__.py`
- Test: `tests/test_keypad.py`

**Interfaces:**
- Consumes: `Settings`, `Action`, `Keypad`/`StatusLed` protocols, `Controller`.
- Produces: `Mcp23017Keypad(settings, bus)` implementing `Keypad`; `Mcp23017Led(settings, bus)` implementing `StatusLed`; `decode_buttons(previous: int, current: int) -> list[Action]`; `build_keypad(settings)` in `main_support`.

**Context:** the eight original buttons map 1:1 to the eight actions. They are read through an MCP23017 on I2C rather than the last eight free GPIOs, which keeps headroom and only costs the two I2C pins. Port A pins 0-7 are the buttons (inputs, pull-ups, active low); port B pin 0 drives the panel LED.

The keypad is polled at 50 Hz rather than using the interrupt line — 20 ms latency is imperceptible and it avoids another GPIO plus interrupt-clearing logic. Revisit only if polling shows up in profiling.

**These buttons work with no network**, which is the point: a physical stop that functions when the API is unreachable.

- [ ] **Step 1: Write the failing test**

Create `tests/test_keypad.py`:

```python
import pytest

from verbot.actions import Action
from verbot.config import Settings
from verbot.hardware.mcp23017 import (
    BUTTON_ORDER,
    Mcp23017Keypad,
    Mcp23017Led,
    decode_buttons,
)
from verbot.hardware.protocols import LedPattern


class FakeBus:
    """Stand-in for smbus2.SMBus."""

    def __init__(self, gpioa: int = 0xFF):
        self.registers: dict[int, int] = {0x12: gpioa, 0x13: 0x00}
        self.writes: list[tuple[int, int]] = []

    def read_byte_data(self, addr: int, register: int) -> int:
        return self.registers.get(register, 0)

    def write_byte_data(self, addr: int, register: int, value: int) -> None:
        self.registers[register] = value
        self.writes.append((register, value))

    def close(self) -> None:
        pass


def test_no_change_yields_no_presses():
    assert decode_buttons(0xFF, 0xFF) == []


def test_a_pressed_button_is_an_active_low_transition():
    """Bit 0 goes 1 -> 0 when the first button is pressed."""
    assert decode_buttons(0xFF, 0xFE) == [BUTTON_ORDER[0]]


def test_release_is_not_reported():
    assert decode_buttons(0xFE, 0xFF) == []


def test_simultaneous_presses_are_all_reported():
    presses = decode_buttons(0xFF, 0b11111100)
    assert presses == [BUTTON_ORDER[0], BUTTON_ORDER[1]]


def test_button_order_covers_every_action():
    assert set(BUTTON_ORDER) == set(Action)
    assert len(BUTTON_ORDER) == 8


async def test_keypad_configures_port_a_as_pulled_up_inputs():
    bus = FakeBus()
    keypad = Mcp23017Keypad(Settings(), bus=bus)
    await keypad.start()
    assert (0x00, 0xFF) in bus.writes  # IODIRA: all inputs
    assert (0x0C, 0xFF) in bus.writes  # GPPUA: all pull-ups
    await keypad.close()


async def test_keypad_emits_presses_on_poll():
    bus = FakeBus()
    seen: list[Action] = []

    async def listener(action: Action) -> None:
        seen.append(action)

    keypad = Mcp23017Keypad(Settings(), bus=bus)
    keypad.set_listener(listener)
    await keypad.start()

    bus.registers[0x12] = 0xFE  # first button down
    await keypad.poll_once()
    bus.registers[0x12] = 0xFF  # released
    await keypad.poll_once()

    assert seen == [BUTTON_ORDER[0]]
    await keypad.close()


async def test_led_solid_sets_the_output_bit():
    bus = FakeBus()
    led = Mcp23017Led(Settings(), bus=bus)
    await led.set_pattern(LedPattern.SOLID)
    assert bus.registers[0x13] & 0x01 == 0x01
    await led.set_pattern(LedPattern.OFF)
    assert bus.registers[0x13] & 0x01 == 0x00
    await led.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_keypad.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'verbot.hardware.mcp23017'`

- [ ] **Step 3: Write the implementation**

Create `src/verbot/hardware/mcp23017.py`:

```python
"""Front-panel keypad and status LED on an MCP23017 I2C expander.

Port A pins 0-7: the eight original red buttons, inputs with pull-ups, active
low. Port B pin 0: the red status LED in the panel's bottom-right corner.

Polled at ~50 Hz rather than wired to the interrupt line - 20 ms is well below
perception and it saves a GPIO plus interrupt-clear handling.
"""

import asyncio
import logging
from typing import Any

from verbot.actions import Action
from verbot.config import Settings
from verbot.hardware.protocols import ButtonListener, LedPattern

log = logging.getLogger(__name__)

# MCP23017 registers (BANK=0)
IODIRA, IODIRB = 0x00, 0x01
GPPUA = 0x0C
GPIOA, GPIOB = 0x12, 0x13

# Physical left-to-right order of the buttons on the front panel.
BUTTON_ORDER: tuple[Action, ...] = (
    Action.STOP,
    Action.FORWARDS,
    Action.REVERSE,
    Action.ROTATE_LEFT,
    Action.ROTATE_RIGHT,
    Action.PICK_UP,
    Action.PUT_DOWN,
    Action.TALK,
)

BLINK_PERIODS = {
    LedPattern.SLOW_BLINK: 0.5,
    LedPattern.FAST_BLINK: 0.12,
}


def decode_buttons(previous: int, current: int) -> list[Action]:
    """Return the buttons that went from released to pressed.

    Inputs are active low, so a press is a 1 -> 0 transition.
    """
    newly_pressed = previous & ~current
    return [BUTTON_ORDER[bit] for bit in range(8) if newly_pressed & (1 << bit)]


def open_bus(settings: Settings) -> Any:
    import smbus2

    return smbus2.SMBus(settings.i2c_bus)


class Mcp23017Keypad:
    def __init__(self, settings: Settings, bus: Any) -> None:
        self._settings = settings
        self._bus = bus
        self._addr = settings.mcp23017_address
        self._listener: ButtonListener | None = None
        self._previous = 0xFF
        self._task: asyncio.Task[None] | None = None

    def set_listener(self, listener: ButtonListener) -> None:
        self._listener = listener

    async def start(self) -> None:
        self._bus.write_byte_data(self._addr, IODIRA, 0xFF)  # port A all inputs
        self._bus.write_byte_data(self._addr, GPPUA, 0xFF)  # with pull-ups
        self._previous = self._bus.read_byte_data(self._addr, GPIOA)
        self._task = asyncio.create_task(self._poll_loop())
        log.info("keypad polling at %.0f Hz", self._settings.keypad_poll_hz)

    async def poll_once(self) -> None:
        current = self._bus.read_byte_data(self._addr, GPIOA)
        pressed = decode_buttons(self._previous, current)
        self._previous = current
        for action in pressed:
            log.info("front panel button: %s", action)
            if self._listener is not None:
                await self._listener(action)

    async def _poll_loop(self) -> None:
        interval = 1.0 / self._settings.keypad_poll_hz
        while True:
            try:
                await self.poll_once()
            except OSError as exc:
                log.warning("keypad read failed: %s", exc)
            await asyncio.sleep(interval)

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None


class Mcp23017Led:
    def __init__(self, settings: Settings, bus: Any) -> None:
        self._bus = bus
        self._addr = settings.mcp23017_address
        self._pattern = LedPattern.OFF
        self._task: asyncio.Task[None] | None = None
        self._bus.write_byte_data(self._addr, IODIRB, 0xFE)  # GPB0 output

    async def set_pattern(self, pattern: LedPattern) -> None:
        if pattern is self._pattern:
            return
        self._pattern = pattern
        self._cancel_blink()

        if pattern is LedPattern.OFF:
            self._write(False)
        elif pattern is LedPattern.SOLID:
            self._write(True)
        else:
            self._task = asyncio.create_task(self._blink(BLINK_PERIODS[pattern]))

    async def _blink(self, period: float) -> None:
        on = False
        while True:
            on = not on
            self._write(on)
            await asyncio.sleep(period)

    def _write(self, on: bool) -> None:
        value = self._bus.read_byte_data(self._addr, GPIOB)
        value = (value | 0x01) if on else (value & ~0x01)
        self._bus.write_byte_data(self._addr, GPIOB, value)

    def _cancel_blink(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def close(self) -> None:
        self._cancel_blink()
        self._write(False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_keypad.py -v`
Expected: 9 passed

- [ ] **Step 5: Wire the keypad into the app**

Add to `src/verbot/main_support.py`:

```python
def build_keypad(settings: Settings):
    """Return (keypad, led), or (None, None) when the keypad is disabled."""
    if not settings.keypad_enabled:
        return None, None
    if not settings.use_real_hardware:
        from verbot.hardware.fakes import FakeKeypad, FakeLed

        return FakeKeypad(), FakeLed()

    from verbot.hardware.mcp23017 import Mcp23017Keypad, Mcp23017Led, open_bus

    bus = open_bus(settings)
    return Mcp23017Keypad(settings, bus=bus), Mcp23017Led(settings, bus=bus)
```

In `src/verbot/__main__.py`, inside `build_app`, after creating `controller`:

```python
    keypad, led = build_keypad(settings)
```

and extend the lifespan body:

```python
    @contextlib.asynccontextmanager
    async def lifespan(app):
        await controller.start()
        if keypad is not None:
            keypad.set_listener(controller.request_action)
            await keypad.start()
        if led is not None:
            await led.set_pattern(LedPattern.SOLID)
        await advertiser.start()
        try:
            yield
        finally:
            await advertiser.close()
            if led is not None:
                await led.set_pattern(LedPattern.OFF)
                await led.close()
            if keypad is not None:
                await keypad.close()
            await controller.close()
            await speech.close()
```

Add the imports `from verbot.hardware.protocols import LedPattern` and `from verbot.main_support import build_hardware, build_keypad`.

Note `keypad.set_listener(controller.request_action)` — the button listener signature is exactly `request_action`'s, so buttons feed the same funnel as the API with no adapter.

- [ ] **Step 6: Run the full suite and commit**

Run: `uv run pytest -v`
Expected: all tests pass

```bash
uv run ruff check . && uv run ruff format .
git add src/verbot/hardware/mcp23017.py src/verbot/main_support.py src/verbot/__main__.py tests/test_keypad.py
git commit -m "feat: front-panel keypad and status LED"
```

---

### Task 11: Deployment and on-Pi bring-up

**Files:**
- Modify: `README.md`
- Create: `docs/deployment.md`
- Modify: `config/verbot.service` (only if the bring-up checklist finds a problem)

**Interfaces:** none — this task produces documentation and a verified deployment.

**This is the first task requiring the physical robot.** Everything before it runs on a laptop.

- [ ] **Step 1: Write the deployment guide**

Create `docs/deployment.md`:

```markdown
# Deploying to the Pi Zero 2 W

## 1. Base image

Flash **64-bit Raspberry Pi OS (Trixie)**. The 64-bit build matters: it gets
prebuilt aarch64 wheels for `pydantic-core`, avoiding a from-source Rust build.

## 2. Firmware config

Append the contents of `config/config.txt.example` to `/boot/firmware/config.txt`
and reboot. Then verify:

```bash
ls /sys/class/pwm/          # expect pwmchip0
aplay -l                    # expect the MAX98357A card
i2cdetect -y 1              # expect a device at 0x20
```

If `pwmchip0` is absent or numbered differently, set `VERBOT_PWM_CHIP`
accordingly — kernel PWM chip numbering has changed between OS releases.

## 3. System packages

```bash
sudo apt update
sudo apt install -y espeak-ng i2c-tools
```

## 4. Install

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/benjamink/verbot-pi-ng.git ~/verbot-pi-ng
cd ~/verbot-pi-ng
uv sync --extra pi --frozen
```

`--frozen` installs exactly the committed `uv.lock`.

## 5. Permissions

PWM and GPIO sysfs access is granted to the `gpio` group by udev rules:

```bash
sudo usermod -aG gpio,i2c,audio "$USER"
```

Log out and back in. If `/sys/class/pwm` writes still fail, check for a udev
rule at `/etc/udev/rules.d/99-pwm.rules`.

## 6. First run

```bash
VERBOT_USE_REAL_HARDWARE=true uv run verbot
```

## 7. Service

```bash
sudo cp config/verbot.service /etc/systemd/system/verbot@.service
sudo systemctl enable --now verbot@$USER
journalctl -u verbot@$USER -f
```

Set `VERBOT_USE_REAL_HARDWARE=true` via an `.env` file in the working
directory, or add `Environment=` lines to the unit.
```

- [ ] **Step 2: Bring-up checklist — run each on the robot, in order**

Work through these with the robot **on a stand with its wheels off the ground**
until step 5 passes.

```bash
# 1. Server starts and reports healthy
curl -s localhost:8080/healthz

# 2. Switches read correctly. Turn the drum BY HAND and watch the log;
#    each switch should log an activate then a release.
journalctl -u verbot@$USER -f

# 3. Motor runs in interrogation direction only (wheels off the ground!)
curl -s -X POST localhost:8080/actions/talk
#    Expect: motor runs anti-clockwise, then reverses when the talk switch
#    closes. If it never reverses, the switch wiring or polarity is wrong.

# 4. Stop works from any state
curl -s -X POST localhost:8080/stop

# 5. Limit switch: raise the arms and confirm they stop at the top
curl -s -X POST localhost:8080/actions/pick_up

# 6. Speech
curl -s -X POST localhost:8080/say -H 'content-type: application/json' \
  -d '{"text":"I am Verbot"}'

# 7. Front panel buttons drive the robot with the network down
sudo ip link set wlan0 down
#    press each button, confirm the action runs
sudo ip link set wlan0 up
```

- [ ] **Step 3: Record measured values**

The interrogation speed of 50% and the 10 s timeout are inherited guesses.
On the real robot, measure and record in `docs/deployment.md`:

- The lowest interrogation speed that still turns the drum reliably (set
  `VERBOT_INTERROGATION_SPEED`). Slower means more accurate switch detection.
- The worst-case time for a full drum revolution. Set
  `VERBOT_INTERROGATION_TIMEOUT_S` to roughly twice that.

- [ ] **Step 4: Update the README status section**

Replace the "Status" section of `README.md` with a short note on what works,
and link `docs/deployment.md` from the "Running on the Pi" section.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/deployment.md config/
git commit -m "docs: deployment guide and bring-up checklist"
```

---

## Phase 2 (not in this plan)

Deliberately deferred. Each needs the phase 1 hardware working first.

**Action durations.** The state machine has no concept of an action
*completing* — only the arms self-terminate via limit switches. `forwards`
runs until something else stops it. Add an optional `duration_s` to
`request_action` and a corresponding API parameter, implemented as a timer
task that requests `STOP`. **This is a prerequisite for sequences.**

**Sequence record and playback.** Queue actions from the keypad, then replay.
Needs durations first. Expose as `GET/POST /sequences` so a physically taught
routine can be fetched and replayed over the API.

**Button chords.** Hold `STOP` + press another button for alternate functions
(speak IP address, shut down, run a demo). Needs press *and release* tracking
in the keypad — `decode_buttons` currently reports presses only.

**Status LED semantics.** Drive `LedPattern` from `Mode`: solid when idle,
slow blink while interrogating, fast blink on `FAULT` or no network.

**Event stream.** `GET /events` as SSE so API clients see physical button
presses and state transitions.
