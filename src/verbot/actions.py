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


# Per docs/hardware.md: only the arm actions have a mechanical limit switch
# wired in series with their drum switch, which breaks the circuit when the
# arm reaches its travel limit. Every other action's drum switch stays
# engaged for as long as the drum is clutched there, so a release seen while
# ACTING is either noise or a genuine ambiguity, not a "done" signal.
ACTIONS_WITH_LIMIT_SWITCH: frozenset[Action] = frozenset({Action.PUT_DOWN, Action.PICK_UP})


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
