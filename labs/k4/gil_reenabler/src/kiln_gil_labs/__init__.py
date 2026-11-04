"""kiln_gil_labs: two C extensions for Kiln's K4 GIL lab.

WHY THIS MODULE EXISTS
    LAB: `kiln_gil_labs.with_gil` has no Py_mod_gil slot, so importing it on a free-threaded
    Python turns the GIL back on; `kiln_gil_labs.without_gil` declares Py_MOD_GIL_NOT_USED and
    keeps it off. See labs/k4/gil_reenabler/README.md and docs/concepts/c-extensions.md.

HOW IT FITS
    labs/k4/wrapper_pkg ──imports──▶ kiln_gil_labs.with_gil ──▶ GIL on ──▶ gilwatch reports it

READ FIRST
    docs/concepts/free-threading.md

MILESTONE
    Introduced in K4.
"""
