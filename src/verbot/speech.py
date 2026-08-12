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
