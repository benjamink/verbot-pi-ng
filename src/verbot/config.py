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
    model_config = SettingsConfigDict(
        env_prefix="VERBOT_", env_file=".env", extra="ignore", env_parse_none_str="null"
    )

    # HTTP server
    host: str = "0.0.0.0"
    port: int = 8080

    # Motor: DRV8833 IN1/IN2 on kernel PWM channels 0 (GPIO 12) and 1 (GPIO 13).
    # The DRV8833 has no PHASE/ENABLE mode, so direction needs two PWM channels
    # rather than one PWM plus a direction pin - see hardware/pwm_motor.py.
    pwm_chip: int = 0
    pwm_channel_a: int = 0  # IN1, positive speeds
    pwm_channel_b: int = 1  # IN2, negative speeds
    pwm_period_ns: int = 4000  # 250 kHz, the DRV8833 maximum
    # nSLEEP (EEP) and nFAULT (ULT) on the carrier. Set the sleep pin to None if
    # the board's J1 jumper is bridged, which ties nSLEEP high in hardware.
    motor_sleep_pin: int | None = 6
    motor_fault_pin: int | None = 16
    interrogation_speed: int = 50
    action_speed: int = -100

    # Interrogation switch bank
    switch_pins: dict[Action, int] = Field(default_factory=lambda: dict(DEFAULT_SWITCH_PINS))
    switch_debounce_us: int = 25_000

    # Safety: give up if the expected switch never arrives.
    interrogation_timeout_s: float = 10.0

    # Speech
    speech_enabled: bool = True
    # Spoken once the server is wired up and serving. Empty string to stay silent.
    startup_announcement: str = "I am Verbot! How may I help you?"
    espeak_voice: str = "en-gb"
    espeak_pitch: int = 10
    espeak_speed: int = 130

    # Front-panel keypad (MCP23017)
    keypad_enabled: bool = True
    i2c_bus: int = 1
    mcp23017_address: int = 0x20
    keypad_poll_hz: float = 50.0
    keypad_debounce_samples: int = 2

    # Shutdown endpoint. Unset means the route is never registered - see
    # docs/deployment.md before turning it on.
    shutdown_token: str | None = None

    @property
    def shutdown_enabled(self) -> bool:
        """A blank token is treated as unset, not as a credential an empty
        header can satisfy. This is the one place that decides "on" so a
        second, differently-worded check can't drift from it."""
        return bool(self.shutdown_token and self.shutdown_token.strip())

    # Hardware toggle: False runs entirely on fakes (dev machines).
    use_real_hardware: bool = False
