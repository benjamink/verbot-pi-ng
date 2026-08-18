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
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            log.error("sudo not found - cannot power off")
            return

        # sudo's stderr is the one diagnostic that distinguishes "user not in
        # sudoers" from "a password is required" - the two failure states
        # named above. It is one short line, so capturing it is not a
        # log-spam risk.
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            log.error(
                "poweroff exited %d - check the sudoers grant in docs/deployment.md: %s",
                proc.returncode,
                stderr.decode(errors="replace").strip(),
            )
