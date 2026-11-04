"""Make the integration helpers' fixture available to every test in this folder.

WHY THIS MODULE EXISTS
    pytest discovers fixtures in conftest.py files. The helpers themselves live in
    tests/support/kiln_helpers.py (on pytest's `pythonpath`) so tests can also import
    `wait_for` and `KilnServer` directly.

HOW IT FITS
    tests/integration/*.py ──fixture kiln_server──▶ tests/support/kiln_helpers.py

READ FIRST
    docs/python/pytest-basics.md

MILESTONE
    Introduced in K1.
"""

from kiln_helpers import kiln_server, start_method

__all__ = ["kiln_server", "start_method"]
