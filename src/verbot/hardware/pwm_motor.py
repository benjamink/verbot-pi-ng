"""Motor driver: two kernel hardware PWM channels (sysfs) driving a DRV8833.

Requires `dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4` in
/boot/firmware/config.txt, which exposes /sys/class/pwm/pwmchip0 with channels
0 and 1.

The DRV8833 has no MODE pin and therefore no PHASE/ENABLE mode: direction is
expressed by *which* of IN1/IN2 carries the PWM while the other sits low, not
by a separate direction level. So both inputs need a PWM channel. Driving one
input high and PWMing the other would give slow-decay braking instead; fast
decay is used here because it keeps the mapping from duty cycle to speed
monotonic, which matters for finding the lowest reliable interrogation speed.

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

# nSLEEP is active low: high wakes the driver, low tri-states the outputs.
AWAKE = 1
ASLEEP = 0

# nFAULT is open-drain, active low, and needs the input's pull-up to read high.
FAULT_LEVEL = 0


class KernelPwmMotor:
    def __init__(
        self,
        settings: Settings,
        sysfs_root: Path = DEFAULT_SYSFS_ROOT,
        gpio: Any | None = None,
    ) -> None:
        self._settings = settings
        self._chip = sysfs_root / f"pwmchip{settings.pwm_chip}"
        # Channel A is IN1 (positive speeds), channel B is IN2 (negative).
        self._channel_a = settings.pwm_channel_a
        self._channel_b = settings.pwm_channel_b
        self._gpio = gpio

    def _path(self, channel: int) -> Path:
        return self._chip / f"pwm{channel}"

    async def open(self) -> None:
        for channel in (self._channel_a, self._channel_b):
            await self._export(channel)
            path = self._path(channel)
            # Duty before period: the kernel rejects a duty longer than the period.
            self._write(path / "duty_cycle", "0")
            self._write(path / "period", str(self._settings.pwm_period_ns))
            self._write(path / "enable", "1")

        if self._gpio is None:
            self._gpio = self._claim_pins()

        if self._settings.motor_sleep_pin is not None:
            self._gpio.write(self._settings.motor_sleep_pin, AWAKE)

        log.info(
            "motor ready: %s channels %d/%d at %d ns period",
            self._chip,
            self._channel_a,
            self._channel_b,
            self._settings.pwm_period_ns,
        )

    async def _export(self, channel: int) -> None:
        path = self._path(channel)
        if path.exists():
            return

        self._write(self._chip / "export", str(channel))
        # The kernel creates the channel directory asynchronously.
        for _ in range(EXPORT_POLL_ATTEMPTS):
            if path.exists():
                return
            await asyncio.sleep(EXPORT_POLL_INTERVAL_S)
        raise RuntimeError(f"PWM channel {path} did not appear after export")

    def _claim_pins(self) -> "_LgpioPins":
        import lgpio

        handle = lgpio.gpiochip_open(0)
        if self._settings.motor_sleep_pin is not None:
            lgpio.gpio_claim_output(handle, self._settings.motor_sleep_pin, ASLEEP)
        if self._settings.motor_fault_pin is not None:
            lgpio.gpio_claim_input(handle, self._settings.motor_fault_pin, lgpio.SET_PULL_UP)
        return _LgpioPins(handle)

    async def set_speed_percent(self, percent: int) -> None:
        if not -100 <= percent <= 100:
            raise ValueError(f"speed {percent} out of range [-100, 100]")

        duty_ns = self._settings.pwm_period_ns * abs(percent) // 100
        if percent < 0:
            active, idle = self._channel_b, self._channel_a
        else:
            active, idle = self._channel_a, self._channel_b

        # Idle first. Both inputs driven at once is a brake, not a direction,
        # and passing through it on every reversal shocks the gearbox.
        self._write(self._path(idle) / "duty_cycle", "0")
        self._write(self._path(active) / "duty_cycle", str(duty_ns))

    async def read_fault(self) -> bool:
        """True while the DRV8833 reports overcurrent, overtemp or undervoltage.

        Latched by the chip until nSLEEP is toggled or the fault clears.
        """
        if self._settings.motor_fault_pin is None or self._gpio is None:
            return False
        return self._gpio.read(self._settings.motor_fault_pin) == FAULT_LEVEL

    async def close(self) -> None:
        for channel in (self._channel_a, self._channel_b):
            try:
                # Zero the duty before disabling; a latched duty can twitch the motor.
                self._write(self._path(channel) / "duty_cycle", "0")
                self._write(self._path(channel) / "enable", "0")
            except OSError as exc:
                log.warning("could not cleanly stop PWM channel %d: %s", channel, exc)

        if self._gpio is not None:
            if self._settings.motor_sleep_pin is not None:
                self._gpio.write(self._settings.motor_sleep_pin, ASLEEP)
            self._gpio.close()

    def _write(self, path: Path, value: str) -> None:
        path.write_text(value)


class _LgpioPins:
    def __init__(self, handle: int) -> None:
        self._handle = handle

    def write(self, pin: int, value: int) -> None:
        import lgpio

        lgpio.gpio_write(self._handle, pin, value)

    def read(self, pin: int) -> int:
        import lgpio

        return lgpio.gpio_read(self._handle, pin)

    def close(self) -> None:
        import lgpio

        lgpio.gpiochip_close(self._handle)
