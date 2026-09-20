"""GPIO output that reports service readiness to external hardware.

Driven high once the controller, front panel and mDNS advertiser are all up,
and low again as soon as shutdown begins, so anything watching the pin sees
exactly the window during which the service can do work.
"""

import logging
from typing import Any

from verbot.config import Settings

log = logging.getLogger(__name__)

NOT_READY = 0
READY = 1


class LgpioReadySignal:
    def __init__(self, settings: Settings, gpio: Any | None = None) -> None:
        pin = settings.ready_pin
        if pin is None:
            raise ValueError("LgpioReadySignal requires settings.ready_pin to be set")
        self._pin = pin
        self._gpio = gpio if gpio is not None else self._claim_pin()

    def _claim_pin(self) -> "_LgpioPin":
        import lgpio

        handle = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_output(handle, self._pin, NOT_READY)
        return _LgpioPin(handle)

    async def set_ready(self, ready: bool) -> None:
        self._gpio.write(self._pin, READY if ready else NOT_READY)
        log.info("ready pin %d -> %s", self._pin, "high" if ready else "low")

    async def close(self) -> None:
        self._gpio.write(self._pin, NOT_READY)
        self._gpio.close()


class _LgpioPin:
    def __init__(self, handle: int) -> None:
        self._handle = handle

    def write(self, pin: int, value: int) -> None:
        import lgpio

        lgpio.gpio_write(self._handle, pin, value)

    def close(self) -> None:
        import lgpio

        lgpio.gpiochip_close(self._handle)
