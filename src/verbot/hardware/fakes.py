"""In-memory hardware. Used by the test suite and by `verbot` on a dev machine."""

from verbot.actions import Action
from verbot.hardware.protocols import ButtonListener, LedPattern, SwitchListener

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
        self.opened = False
        self.closed = False

    async def open(self) -> None:
        self.opened = True

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
