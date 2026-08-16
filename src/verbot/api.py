"""HTTP control surface.

Typing the path parameter as `Action` gets validation for free: an unknown
action is a 422 rather than a silently ignored request.
"""

import secrets
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator

from verbot.actions import Action, ControllerStatus
from verbot.config import Settings
from verbot.controller import Controller
from verbot.hardware.protocols import SpeechEngine, SystemPower
from verbot.web import render_index


class SayRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)

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
) -> FastAPI:
    app = FastAPI(
        title="Verbot",
        description="Control a 1984 Tomy Verbot toy robot.",
        version="0.1.0",
    )
    app.state.controller = controller
    app.state.speech = speech

    index_html = render_index(list(Action))

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
    async def say(body: SayRequest, speech: SpeechDep) -> dict[str, str]:
        await speech.say(body.text)
        return {"spoken": body.text}

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

    return app
