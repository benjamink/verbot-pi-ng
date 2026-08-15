import pytest

from verbot.config import Settings
from verbot.hardware.pwm_motor import KernelPwmMotor

CHANNEL_NAMES = ("pwm0", "pwm1")
CHANNEL_FILES = ("period", "duty_cycle", "enable", "polarity")


class FakeGpio:
    def __init__(self, fault_level: int = 1):
        self.writes: list[tuple[int, int]] = []
        self.fault_level = fault_level
        self.closed = False

    def write(self, pin: int, value: int) -> None:
        self.writes.append((pin, value))

    def read(self, pin: int) -> int:
        return self.fault_level

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def sysfs(tmp_path):
    """A minimal fake of /sys/class/pwm with one chip and both channels.

    `dtoverlay=pwm-2chan` gives pwm0 (IN1) and pwm1 (IN2) on pwmchip0.
    """
    chip = tmp_path / "pwmchip0"
    (chip / "export").parent.mkdir(parents=True, exist_ok=True)
    (chip / "export").write_text("")
    (chip / "unexport").write_text("")
    for name in CHANNEL_NAMES:
        channel = chip / name
        channel.mkdir()
        for filename in CHANNEL_FILES:
            (channel / filename).write_text("0")
    return tmp_path


@pytest.fixture
def gpio():
    return FakeGpio()


@pytest.fixture
def motor(sysfs, gpio):
    return KernelPwmMotor(Settings(), sysfs_root=sysfs, gpio=gpio)


def read(sysfs, channel: str, name: str) -> str:
    return (sysfs / "pwmchip0" / channel / name).read_text().strip()


class RecordingMotor(KernelPwmMotor):
    """Captures the order of sysfs writes, which the files alone cannot show."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.writes: list[tuple[str, str, str]] = []

    def _write(self, path, value: str) -> None:
        super()._write(path, value)
        self.writes.append((path.parent.name, path.name, value))


async def test_open_enables_both_channels(motor, sysfs):
    await motor.open()
    for channel in CHANNEL_NAMES:
        assert read(sysfs, channel, "period") == "4000"  # 250 kHz
        assert read(sysfs, channel, "enable") == "1"
        assert read(sysfs, channel, "duty_cycle") == "0"


async def test_forward_drives_channel_a_only(motor, sysfs):
    """IN1 carries the PWM, IN2 sits low: the DRV8833's forward, fast decay."""
    await motor.open()
    await motor.set_speed_percent(100)
    assert read(sysfs, "pwm0", "duty_cycle") == "4000"
    assert read(sysfs, "pwm1", "duty_cycle") == "0"


async def test_reverse_drives_channel_b_only(motor, sysfs):
    await motor.open()
    await motor.set_speed_percent(-100)
    assert read(sysfs, "pwm0", "duty_cycle") == "0"
    assert read(sysfs, "pwm1", "duty_cycle") == "4000"


async def test_half_speed_is_half_duty(motor, sysfs):
    await motor.open()
    await motor.set_speed_percent(50)
    assert read(sysfs, "pwm0", "duty_cycle") == "2000"


async def test_zero_speed_coasts_both_channels(motor, sysfs):
    await motor.open()
    await motor.set_speed_percent(100)
    await motor.set_speed_percent(0)
    assert read(sysfs, "pwm0", "duty_cycle") == "0"
    assert read(sysfs, "pwm1", "duty_cycle") == "0"


async def test_reversing_zeroes_the_old_channel_before_driving_the_new(sysfs, gpio):
    """Both inputs high is a brake, not a direction. Never pass through it."""
    motor = RecordingMotor(Settings(), sysfs_root=sysfs, gpio=gpio)
    await motor.open()
    await motor.set_speed_percent(100)
    motor.writes.clear()

    await motor.set_speed_percent(-100)

    duty = [(channel, value) for channel, name, value in motor.writes if name == "duty_cycle"]
    assert duty == [("pwm0", "0"), ("pwm1", "4000")]


async def test_out_of_range_is_rejected(motor):
    await motor.open()
    with pytest.raises(ValueError):
        await motor.set_speed_percent(-101)


async def test_open_wakes_the_driver(motor, gpio):
    """nSLEEP is active low: the outputs stay tri-stated until it is driven high."""
    await motor.open()
    assert gpio.writes == [(Settings().motor_sleep_pin, 1)]


async def test_close_sleeps_the_driver(motor, gpio, sysfs):
    await motor.open()
    await motor.set_speed_percent(100)
    await motor.close()

    assert gpio.writes[-1] == (Settings().motor_sleep_pin, 0)
    assert gpio.closed
    for channel in CHANNEL_NAMES:
        assert read(sysfs, channel, "duty_cycle") == "0"
        assert read(sysfs, channel, "enable") == "0"


async def test_no_sleep_pin_leaves_the_gpio_untouched(sysfs, gpio):
    """J1 bridged on the carrier ties nSLEEP high in hardware; nothing to drive."""
    motor = KernelPwmMotor(Settings(motor_sleep_pin=None), sysfs_root=sysfs, gpio=gpio)
    await motor.open()
    assert gpio.writes == []


async def test_fault_is_reported_when_the_pin_reads_low(sysfs):
    """nFAULT is open-drain, active low: overcurrent, overtemp or undervoltage."""
    motor = KernelPwmMotor(Settings(), sysfs_root=sysfs, gpio=FakeGpio(fault_level=0))
    await motor.open()
    assert await motor.read_fault() is True


async def test_no_fault_when_the_pin_reads_high(motor):
    await motor.open()
    assert await motor.read_fault() is False


async def test_read_fault_is_false_when_no_fault_pin_is_wired(sysfs, gpio):
    motor = KernelPwmMotor(
        Settings(motor_fault_pin=None), sysfs_root=sysfs, gpio=FakeGpio(fault_level=0)
    )
    await motor.open()
    assert await motor.read_fault() is False


class ExportingMotor(KernelPwmMotor):
    """Simulates the kernel materialising pwmN when `export` is written."""

    def _write(self, path, value: str) -> None:
        super()._write(path, value)
        if path.name == "export":
            channel = path.parent / f"pwm{value}"
            channel.mkdir(exist_ok=True)
            for name in CHANNEL_FILES:
                (channel / name).write_text("0")


async def test_open_exports_both_channels_when_absent(tmp_path, gpio):
    """On a fresh boot neither pwmN directory exists until exported."""
    chip = tmp_path / "pwmchip0"
    chip.mkdir(parents=True)
    (chip / "export").write_text("")
    (chip / "unexport").write_text("")

    motor = ExportingMotor(Settings(), sysfs_root=tmp_path, gpio=gpio)
    await motor.open()

    for channel in CHANNEL_NAMES:
        assert (chip / channel / "enable").read_text().strip() == "1"


async def test_open_fails_loudly_if_a_channel_never_appears(tmp_path, gpio):
    """Better a clear error than a silent no-op motor."""
    chip = tmp_path / "pwmchip0"
    chip.mkdir(parents=True)
    (chip / "export").write_text("")

    motor = KernelPwmMotor(Settings(), sysfs_root=tmp_path, gpio=gpio)
    with pytest.raises(RuntimeError, match="did not appear"):
        await motor.open()
