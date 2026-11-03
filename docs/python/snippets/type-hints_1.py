# Run with: python docs/python/snippets/type-hints_1.py
# Python itself ignores type hints when running. A type checker (mypy) reads them.


def port_from(text: str) -> int:
    return int(text)


def describe(port: int) -> str:
    return "listening on port " + str(port)


print(describe(port_from("7340")))
# The next line is WRONG according to the hints (a str where an int is expected),
# but Python still runs it, because hints are not checked at run time.
print(describe("7340"))  # type: ignore[arg-type]
