import pytest

from verbot.config import Settings
from verbot.hardware.pwm_motor import KernelPwmMotor


class FakeGpio:
    def __init__(self):
        self.values: list[int] = []
        self.closed = False

    def write(self, pin: int, value: int) -> None:
        self.values.append(value)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def sysfs(tmp_path):
    """A minimal fake of /sys/class/pwm with one chip and one channel."""
    chip = tmp_path / "pwmchip0"
    channel = chip / "pwm0"
    channel.mkdir(parents=True)
    (chip / "export").write_text("")
    (chip / "unexport").write_text("")
    for name in ("period", "duty_cycle", "enable", "polarity"):
        (channel / name).write_text("0")
    return tmp_path


@pytest.fixture
def motor(sysfs):
    return KernelPwmMotor(Settings(), sysfs_root=sysfs, gpio=FakeGpio())


def read(sysfs, name: str) -> str:
    return (sysfs / "pwmchip0" / "pwm0" / name).read_text().strip()


async def test_open_sets_period_and_enables(motor, sysfs):
    await motor.open()
    assert read(sysfs, "period") == "4000"  # 250 kHz
    assert read(sysfs, "enable") == "1"
    assert read(sysfs, "duty_cycle") == "0"


async def test_full_forward_is_full_duty(motor, sysfs):
    await motor.open()
    await motor.set_speed_percent(100)
    assert read(sysfs, "duty_cycle") == "4000"


async def test_half_speed_is_half_duty(motor, sysfs):
    await motor.open()
    await motor.set_speed_percent(50)
    assert read(sysfs, "duty_cycle") == "2000"


async def test_negative_speed_sets_direction_and_positive_duty(motor, sysfs):
    await motor.open()
    await motor.set_speed_percent(-100)
    assert read(sysfs, "duty_cycle") == "4000"
    assert motor._gpio.values[-1] == 1


async def test_positive_speed_clears_direction(motor, sysfs):
    await motor.open()
    await motor.set_speed_percent(50)
    assert motor._gpio.values[-1] == 0


async def test_out_of_range_is_rejected(motor):
    await motor.open()
    with pytest.raises(ValueError):
        await motor.set_speed_percent(-101)


async def test_close_zeroes_duty_before_disabling(motor, sysfs):
    """Leaving a duty cycle latched while disabling can twitch the motor."""
    await motor.open()
    await motor.set_speed_percent(100)
    await motor.close()
    assert read(sysfs, "duty_cycle") == "0"
    assert read(sysfs, "enable") == "0"
    assert motor._gpio.closed


class ExportingMotor(KernelPwmMotor):
    """Simulates the kernel materialising pwmN when `export` is written."""

    def _write(self, path, value: str) -> None:
        super()._write(path, value)
        if path.name == "export":
            channel = path.parent / f"pwm{value}"
            channel.mkdir(exist_ok=True)
            for name in ("period", "duty_cycle", "enable", "polarity"):
                (channel / name).write_text("0")


async def test_open_exports_the_channel_when_absent(tmp_path):
    """On a fresh boot the pwmN directory does not exist until exported."""
    chip = tmp_path / "pwmchip0"
    chip.mkdir(parents=True)
    (chip / "export").write_text("")
    (chip / "unexport").write_text("")
    assert not (chip / "pwm0").exists()

    motor = ExportingMotor(Settings(), sysfs_root=tmp_path, gpio=FakeGpio())
    await motor.open()

    assert (chip / "export").read_text().strip() == "0"
    assert (chip / "pwm0" / "enable").read_text().strip() == "1"


async def test_open_fails_loudly_if_the_channel_never_appears(tmp_path):
    """Better a clear error than a silent no-op motor."""
    chip = tmp_path / "pwmchip0"
    chip.mkdir(parents=True)
    (chip / "export").write_text("")

    motor = KernelPwmMotor(Settings(), sysfs_root=tmp_path, gpio=FakeGpio())
    with pytest.raises(RuntimeError, match="did not appear"):
        await motor.open()
