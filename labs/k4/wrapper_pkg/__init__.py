"""wrapper_pkg: a stand-in for "a library you use, which imports a legacy C extension".

WHY THIS MODULE EXISTS
    LAB: the culprit is rarely imported directly; it hides behind a dependency. Importing this
    package imports kiln_gil_labs.with_gil, so gilwatch must report the chain
    wrapper_pkg>kiln_gil_labs.with_gil (K4 step 5).

HOW IT FITS
    app ──import wrapper_pkg──▶ wrapper_pkg ──import──▶ kiln_gil_labs.with_gil (no Py_mod_gil)

READ FIRST
    docs/concepts/free-threading.md

MILESTONE
    Introduced in K4.
"""

from kiln_gil_labs import with_gil

add = with_gil.add
