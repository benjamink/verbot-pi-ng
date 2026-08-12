from verbot.actions import Action
from verbot.config import Settings
from verbot.hardware.mcp23017 import (
    BUTTON_ORDER,
    GPIOA,
    GPIOB,
    GPPUA,
    IODIRA,
    Mcp23017Keypad,
    Mcp23017Led,
    decode_buttons,
)
from verbot.hardware.protocols import LedPattern


class FakeBus:
    """Stand-in for smbus2.SMBus."""

    def __init__(self, gpioa: int = 0xFF):
        self.registers: dict[int, int] = {GPIOA: gpioa, GPIOB: 0x00}
        self.writes: list[tuple[int, int]] = []

    def read_byte_data(self, addr: int, register: int) -> int:
        return self.registers.get(register, 0)

    def write_byte_data(self, addr: int, register: int, value: int) -> None:
        self.registers[register] = value
        self.writes.append((register, value))

    def close(self) -> None:
        pass


def test_no_change_yields_no_presses():
    assert decode_buttons(0xFF, 0xFF) == []


def test_a_pressed_button_is_an_active_low_transition():
    """Bit 0 goes 1 -> 0 when the first button is pressed."""
    assert decode_buttons(0xFF, 0xFE) == [BUTTON_ORDER[0]]


def test_release_is_not_reported():
    assert decode_buttons(0xFE, 0xFF) == []


def test_simultaneous_presses_are_all_reported():
    presses = decode_buttons(0xFF, 0b11111100)
    assert presses == [BUTTON_ORDER[0], BUTTON_ORDER[1]]


def test_button_order_covers_every_action():
    assert set(BUTTON_ORDER) == set(Action)
    assert len(BUTTON_ORDER) == 8


async def test_keypad_configures_port_a_as_pulled_up_inputs():
    bus = FakeBus()
    keypad = Mcp23017Keypad(Settings(), bus=bus)
    await keypad.start()
    assert (IODIRA, 0xFF) in bus.writes  # all inputs
    assert (GPPUA, 0xFF) in bus.writes  # with pull-ups
    await keypad.close()


async def test_keypad_emits_presses_on_poll():
    bus = FakeBus()
    seen: list[Action] = []

    async def listener(action: Action) -> None:
        seen.append(action)

    keypad = Mcp23017Keypad(Settings(), bus=bus)
    keypad.set_listener(listener)
    await keypad.start()

    bus.registers[GPIOA] = 0xFE  # first button down
    await keypad.poll_once()
    bus.registers[GPIOA] = 0xFF  # released
    await keypad.poll_once()

    assert seen == [BUTTON_ORDER[0]]
    await keypad.close()


async def test_holding_a_button_reports_one_press_not_many():
    """At 50 Hz a normal press spans several polls."""
    bus = FakeBus()
    seen: list[Action] = []

    async def listener(action: Action) -> None:
        seen.append(action)

    keypad = Mcp23017Keypad(Settings(), bus=bus)
    keypad.set_listener(listener)
    await keypad.start()

    bus.registers[GPIOA] = 0xFE
    for _ in range(5):
        await keypad.poll_once()

    assert seen == [BUTTON_ORDER[0]]
    await keypad.close()


async def test_led_solid_sets_the_output_bit():
    bus = FakeBus()
    led = Mcp23017Led(Settings(), bus=bus)
    await led.set_pattern(LedPattern.SOLID)
    assert bus.registers[GPIOB] & 0x01 == 0x01
    await led.set_pattern(LedPattern.OFF)
    assert bus.registers[GPIOB] & 0x01 == 0x00
    await led.close()
