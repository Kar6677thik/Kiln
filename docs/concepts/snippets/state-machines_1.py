"""A transition table as code: every allowed (from, to) pair, and one function that changes state."""

from enum import StrEnum


class Light(StrEnum):
    OFF = "OFF"
    ON = "ON"
    BROKEN = "BROKEN"


# The whole design in one table. Anything not listed is a bug.
ALLOWED = {
    (Light.OFF, Light.ON): "switch on",
    (Light.ON, Light.OFF): "switch off",
    (Light.ON, Light.BROKEN): "bulb burns out",
}
TERMINAL = {Light.BROKEN}


def transition(state, to):
    if (state, to) not in ALLOWED:
        raise AssertionError(f"illegal transition {state} -> {to}")
    print(f"{state} -> {to}  ({ALLOWED[(state, to)]})")
    return to


state = Light.OFF
state = transition(state, Light.ON)
state = transition(state, Light.OFF)
state = transition(state, Light.ON)
state = transition(state, Light.BROKEN)
print("terminal?", state in TERMINAL)
try:
    transition(state, Light.ON)
except AssertionError as error:
    print("refused:", error)
