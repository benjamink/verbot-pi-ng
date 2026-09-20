"""HTTP control surface.

Typing the path parameter as `Action` gets validation for free: an unknown
action is a 422 rather than a silently ignored request.
"""

import asyncio
import logging
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator

from verbot.actions import Action, ControllerStatus
from verbot.config import Settings
from verbot.controller import Controller
from verbot.hardware.protocols import ReadySignal, SpeechEngine, SystemPower
from verbot.web import render_index

log = logging.getLogger(__name__)


class SayRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    # Off by default so a bare /say stays a pure speaker test: bring-up step 6
    # needs audio without moving a mechanism that may not be attached yet.
    animate: bool = False

    @field_validator("text")
    @classmethod
    def not_only_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class Speeds(BaseModel):
    interrogation_speed: int
    action_speed: int


class SpeedsPatch(BaseModel):
    """Both optional so a slider can send only the value it changed.

    Bounds match set_speed_percent's, so a bad value is a 422 here rather than
    a ValueError raised mid-action with the motor already turning.
    """

    interrogation_speed: int | None = Field(default=None, ge=0, le=100)
    action_speed: int | None = Field(default=None, ge=-100, le=100)


# International Morse, restricted to the letters BLINK_WORD actually uses.
MORSE_CODE: dict[str, str] = {
    "V": "...-",
    "E": ".",
    "R": ".-.",
    "B": "-...",
    "O": "---",
    "T": "-",
}
BLINK_WORD = "VERBOT"

# Dit length. Dah is 3x, the gap between symbols in a letter is 1x, and the
# gap between letters is 3x - standard Morse timing (the PARIS ratios), just
# slow enough to read by eye rather than by ear.
MORSE_UNIT_S = 0.2


async def blink_ready_signal(ready_signal: ReadySignal, word: str = BLINK_WORD) -> None:
    """Blink `word` out in Morse code, ending lit to match the steady "ready" state.

    Fire-and-forget from the route: the response should not block on the
    several seconds this takes.
    """
    for letter in word:
        symbols = MORSE_CODE[letter]
        for index, symbol in enumerate(symbols):
            await ready_signal.set_ready(True)
            await asyncio.sleep(MORSE_UNIT_S if symbol == "." else 3 * MORSE_UNIT_S)
            await ready_signal.set_ready(False)
            if index < len(symbols) - 1:
                await asyncio.sleep(MORSE_UNIT_S)  # gap between symbols
        await asyncio.sleep(3 * MORSE_UNIT_S)  # gap between letters
    await ready_signal.set_ready(True)


def get_controller(request: Request) -> Controller:
    return request.app.state.controller


def get_speech(request: Request) -> SpeechEngine:
    return request.app.state.speech


ControllerDep = Annotated[Controller, Depends(get_controller)]
SpeechDep = Annotated[SpeechEngine, Depends(get_speech)]


def create_app(
    controller: Controller,
    speech: SpeechEngine,
    settings: Settings,
    power: SystemPower,
    ready_signal: ReadySignal | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Verbot",
        description="Control a 1984 Tomy Verbot toy robot.",
        version="0.1.0",
    )
    app.state.controller = controller
    app.state.speech = speech

    index_html = render_index(
        list(Action),
        shutdown_enabled=settings.shutdown_enabled,
        blink_led_enabled=ready_signal is not None,
    )

    @app.get("/", include_in_schema=False, response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        """The bring-up control page. Rendered once at startup, not per request."""
        return HTMLResponse(index_html)

    @app.get("/healthz", tags=["system"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/speeds", tags=["control"], response_model=Speeds)
    async def get_speeds() -> Speeds:
        return Speeds(
            interrogation_speed=settings.interrogation_speed,
            action_speed=settings.action_speed,
        )

    @app.patch("/speeds", tags=["control"], response_model=Speeds)
    async def patch_speeds(body: SpeedsPatch) -> Speeds:
        """Adjust speeds live, for the measurements docs/deployment.md asks for.

        The controller reads these off Settings each time it drives the motor,
        so a change lands on the next action. Deliberately not persisted: the
        values worth keeping belong in .env once measured.
        """
        if body.interrogation_speed is not None:
            settings.interrogation_speed = body.interrogation_speed
        if body.action_speed is not None:
            settings.action_speed = body.action_speed
        return Speeds(
            interrogation_speed=settings.interrogation_speed,
            action_speed=settings.action_speed,
        )

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
        """Begin interrogating for `action`.

        Returns once the motor starts, not once the action completes.
        """
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

    @app.post(
        "/halt",
        tags=["control"],
        status_code=status.HTTP_202_ACCEPTED,
        response_model=ControllerStatus,
    )
    async def halt(controller: ControllerDep) -> ControllerStatus:
        """Cut the motor now, without interrogating.

        Distinct from /stop, which drives the drum round to the stop cam and
        takes seconds of movement to get there. This is what an emergency
        control should be bound to.
        """
        await controller.halt()
        return controller.status

    @app.post("/say", tags=["speech"], status_code=status.HTTP_202_ACCEPTED)
    async def say(
        body: SayRequest, controller: ControllerDep, speech: SpeechDep
    ) -> dict[str, str | bool]:
        """Speak `text`, optionally animating the mouth while it plays.

        With `animate`, the talk gear has to be engaged *before* the phrase
        starts or the mouth and the audio will not line up, so this waits for
        the interrogation to reach ACTING. Talk has no limit switch — per
        docs/hardware.md the gear set runs until the motor reverses — so the
        drum is parked at the stop cam once the phrase ends.

        A mouth that never engages is logged and then ignored: the audio is the
        point, and refusing to speak because the gearbox is absent would make
        the speaker untestable on the bench.
        """
        animated = False
        if body.animate:
            await controller.request_action(Action.TALK)
            animated = await controller.wait_until_acting(
                Action.TALK, timeout=settings.interrogation_timeout_s + 1.0
            )
            if not animated:
                log.warning("talk gear never engaged; speaking with a still mouth")

        await speech.say(body.text)

        if animated:
            await controller.request_action(Action.STOP)
        return {"spoken": body.text, "animated": animated}

    if ready_signal is not None:
        # Registered only when a ready pin is configured, same as /system/shutdown
        # below - a route that can never do anything is worse than no route.
        @app.post("/system/blink-led", tags=["system"], status_code=status.HTTP_202_ACCEPTED)
        async def blink_led(background: BackgroundTasks) -> dict[str, str]:
            """Blink "VERBOT" in Morse on the ready-pin LED, to spot this Pi among others."""
            background.add_task(blink_ready_signal, ready_signal)
            return {"status": "blinking"}

    if settings.shutdown_enabled:

        @app.post("/system/shutdown", tags=["system"], status_code=status.HTTP_202_ACCEPTED)
        async def shutdown(
            background: BackgroundTasks, controller: ControllerDep
        ) -> dict[str, str]:
            """Power the machine off.

            Registered only when enabled, so the default deployment has no such
            route at all. Unauthenticated, like the rest of the API - anyone who
            can reach it can already drive the robot; the web UI's confirm()
            prompt is a guard against misclicks, not against a hostile network.
            """
            # Stop the robot before the machine goes: systemd's teardown would
            # get there eventually, but not for a few hundred milliseconds, and
            # not at all if the poweroff itself fails.
            await controller.halt()
            # A background task runs after the response is sent, so the 202
            # reaches the caller rather than dying with the machine.
            background.add_task(power.shutdown)
            return {"status": "shutting down"}

    return app
