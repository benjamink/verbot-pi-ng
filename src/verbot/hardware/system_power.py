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
