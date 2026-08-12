"""Runtime configuration. Every field is overridable via VERBOT_* env vars."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from verbot.actions import Action

# BCM pin per action. Unchanged from the original wiring - see docs/hardware.md.
DEFAULT_SWITCH_PINS: dict[Action, int] = {
    Action.STOP: 22,
    Action.ROTATE_RIGHT: 26,
    Action.ROTATE_LEFT: 10,
    Action.FORWARDS: 9,
    Action.REVERSE: 25,
    Action.PUT_DOWN: 11,
    Action.PICK_UP: 8,
    Action.TALK: 7,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VERBOT_", env_file=".env", extra="ignore")

    # HTTP server
    host: str = "0.0.0.0"
    port: int = 8080

    # Motor: kernel PWM on GPIO 12 (PWM0), direction on GPIO 5.
    pwm_chip: int = 0
    pwm_channel: int = 0
    pwm_period_ns: int = 4000  # 250 kHz, the DRV8835 maximum
    motor_dir_pin: int = 5
    interrogation_speed: int = 50
    action_speed: int = -100

    # Interrogation switch bank
    switch_pins: dict[Action, int] = Field(default_factory=lambda: dict(DEFAULT_SWITCH_PINS))
    switch_debounce_us: int = 25_000

    # Safety: give up if the expected switch never arrives.
    interrogation_timeout_s: float = 10.0

    # Speech
    speech_enabled: bool = True
    espeak_voice: str = "en-gb"
    espeak_pitch: int = 10
    espeak_speed: int = 130

    # Front-panel keypad (MCP23017)
    keypad_enabled: bool = True
    i2c_bus: int = 1
    mcp23017_address: int = 0x20
    keypad_poll_hz: float = 50.0
    keypad_debounce_samples: int = 2

    # Hardware toggle: False runs entirely on fakes (dev machines).
    use_real_hardware: bool = False
