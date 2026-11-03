# Check with: mypy docs/python/snippets/type-hints_2.py   (do not run; it is for the type checker)
# The same mistake as type-hints_1.py, but without the "type: ignore" comment,
# so mypy reports it.


def describe(port: int) -> str:
    return "listening on port " + str(port)


describe("7340")
