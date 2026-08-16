"""Entrypoint. Wires hardware, controller, API and mDNS together."""

import asyncio
import contextlib
import logging

import uvicorn
from fastapi import FastAPI

from verbot.api import create_app
from verbot.config import Settings
from verbot.controller import Controller
from verbot.discovery import ServiceAdvertiser
from verbot.hardware.protocols import LedPattern
from verbot.main_support import build_hardware, build_keypad, build_power
from verbot.speech import EspeakEngine

log = logging.getLogger(__name__)


async def announce(speech: EspeakEngine, text: str) -> None:
    """Speak the readiness greeting.

    Runs detached from startup: the phrase takes a couple of seconds, and the
    API and front panel should not wait on the sound card to become useful.
    A failure here is cosmetic, so it is logged rather than propagated.
    """
    try:
        await speech.say(text)
    except Exception:
        log.exception("startup announcement failed")


def build_app(settings: Settings) -> FastAPI:
    motor, switches = build_hardware(settings)
    controller = Controller(motor=motor, switches=switches, settings=settings)
    keypad, led = build_keypad(settings)
    power = build_power(settings)
    speech = EspeakEngine(settings)
    advertiser = ServiceAdvertiser(settings)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        await controller.start()
        if keypad is not None:
            # The button listener signature is exactly request_action's, so the
            # front panel feeds the same funnel as the API with no adapter.
            keypad.set_listener(controller.request_action)
            await keypad.start()
        if led is not None:
            await led.set_pattern(LedPattern.SOLID)
        await advertiser.start()
        # Last, so "ready" means the controller, panel and mDNS are all up.
        announcement: asyncio.Task[None] | None = None
        if settings.startup_announcement:
            announcement = asyncio.create_task(announce(speech, settings.startup_announcement))
        app.state.announcement = announcement
        try:
            yield
        finally:
            # Order matters: stop advertising, then guarantee the motor is off.
            if announcement is not None:
                announcement.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await announcement
            await advertiser.close()
            if led is not None:
                await led.set_pattern(LedPattern.OFF)
                await led.close()
            if keypad is not None:
                await keypad.close()
            await controller.close()
            await speech.close()

    app = create_app(controller=controller, speech=speech, settings=settings, power=power)
    app.router.lifespan_context = lifespan
    # Expose the composed hardware for introspection and tests.
    app.state.motor = motor
    app.state.switches = switches
    app.state.keypad = keypad
    app.state.led = led
    app.state.power = power
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
