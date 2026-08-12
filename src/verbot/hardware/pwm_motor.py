"""Motor driver: kernel hardware PWM (sysfs) + a GPIO direction pin.

Requires `dtoverlay=pwm,pin=12,func=4` in /boot/firmware/config.txt, which
exposes /sys/class/pwm/pwmchip0.

Kernel PWM is used instead of pigpio because pigpio needs the PCM peripheral
for DMA timing in order to leave hardware PWM free - and the I2S DAC needs PCM.
The kernel PWM driver has no such conflict, so the robot gets both true 250 kHz
PWM and audio. The original project had to abandon hardware PWM for exactly
this reason.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

from verbot.config import Settings

log = logging.getLogger(__name__)

DEFAULT_SYSFS_ROOT = Path("/sys/class/pwm")

# How long to wait for the kernel to create the channel directory after export.
EXPORT_POLL_INTERVAL_S = 0.01
EXPORT_POLL_ATTEMPTS = 50


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

    async def open(self) -> None:
        if not self._channel.exists():
            self._write(self._chip / "export", str(self._settings.pwm_channel))
            # The kernel creates the channel directory asynchronously.
            for _ in range(EXPORT_POLL_ATTEMPTS):
                if self._channel.exists():
                    break
                await asyncio.sleep(EXPORT_POLL_INTERVAL_S)
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
        duty_ns = self._settings.pwm_period_ns * abs(percent) // 100

        # Direction first: changing it under load is harder on the H-bridge.
        self._gpio.write(self._settings.motor_dir_pin, direction)
        self._write(self._channel / "duty_cycle", str(duty_ns))

    async def close(self) -> None:
        try:
            # Zero the duty before disabling; a latched duty can twitch the motor.
            self._write(self._channel / "duty_cycle", "0")
            self._write(self._channel / "enable", "0")
        except OSError as exc:
            log.warning("could not cleanly stop PWM: %s", exc)
        if self._gpio is not None:
            self._gpio.close()

    def _write(self, path: Path, value: str) -> None:
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
