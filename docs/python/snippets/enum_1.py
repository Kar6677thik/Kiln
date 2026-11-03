# Why enums beat "magic strings": a typo in a string is silently wrong,
# a typo in an enum member is an immediate, loud error.
from enum import StrEnum


class Status(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    INFO = "info"


SYMBOL = {Status.PASS: "OK", Status.WARN: "!!", Status.FAIL: "XX", Status.INFO: "--"}

result = "fial"  # typo in a plain string: nothing complains
print("string typo equals 'fail'?", result == "fail")

try:
    result = Status.FIAL  # typo in an enum member
except AttributeError as error:
    print("enum typo:", error)

for status in Status:
    print(f"{status.name:<5} value={status.value!r:<7} shown as {SYMBOL[status]}")

print("from JSON text back to a member:", Status("warn"), Status("warn") is Status.WARN)
