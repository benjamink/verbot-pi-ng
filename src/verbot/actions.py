"""Domain vocabulary for the Verbot gearbox."""

from enum import StrEnum

from pydantic import BaseModel


class Action(StrEnum):
    """The eight gearbox positions, in interrogation drum order."""

    STOP = "stop"
    ROTATE_RIGHT = "rotate_right"
    ROTATE_LEFT = "rotate_left"
    FORWARDS = "forwards"
    REVERSE = "reverse"
    PUT_DOWN = "put_down"
    PICK_UP = "pick_up"
    TALK = "talk"


class Mode(StrEnum):
    """What the controller is currently doing."""

    IDLE = "idle"
    INTERROGATING = "interrogating"
    ACTING = "acting"
    FAULT = "fault"


class ControllerStatus(BaseModel):
    mode: Mode
    current_action: Action | None
    desired_action: Action | None
