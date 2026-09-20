import pytest

from verbot.config import Settings
from verbot.hardware.ready_pin import LgpioReadySignal


class FakeGpio:
    def __init__(self) -> None:
        self.writes: list[tuple[int, int]] = []
        self.closed = False

    def write(self, pin: int, value: int) -> None:
        self.writes.append((pin, value))

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def gpio():
    return FakeGpio()


async def test_set_ready_true_drives_the_pin_high(gpio):
    signal = LgpioReadySignal(Settings(ready_pin=17), gpio=gpio)
    await signal.set_ready(True)
    assert gpio.writes == [(17, 1)]


async def test_set_ready_false_drives_the_pin_low(gpio):
    signal = LgpioReadySignal(Settings(ready_pin=17), gpio=gpio)
    await signal.set_ready(False)
    assert gpio.writes == [(17, 0)]


async def test_close_drops_the_pin_and_releases_the_chip(gpio):
    signal = LgpioReadySignal(Settings(ready_pin=17), gpio=gpio)
    await signal.set_ready(True)
    await signal.close()
    assert gpio.writes[-1] == (17, 0)
    assert gpio.closed


def test_requires_a_configured_pin(gpio):
    with pytest.raises(ValueError, match="ready_pin"):
        LgpioReadySignal(Settings(ready_pin=None), gpio=gpio)
