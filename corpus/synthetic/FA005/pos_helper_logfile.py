"""FA005 positive (helper): opens a file for append at import; pos_imports_helper imports it."""

# WHY: checker input written like everyday user code (no docstrings or types).
# ruff: noqa: ANN001, ANN201, D103, PTH123, S108, SIM115

LOG = open("/tmp/kiln-synthetic.log", "a")  # expect: FA005


def note(text):
    LOG.write(text + "\n")
