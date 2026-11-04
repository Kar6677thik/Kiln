/*
 * kiln_gil_labs.with_gil: a tiny C extension that does NOT declare free-threading support.
 *
 * WHY THIS FILE EXISTS
 *   LAB: on a free-threaded Python (3.15t), importing a module like this one quietly turns
 *   the GIL back on for the whole process (docs/concepts/free-threading.md). Kiln's
 *   gilwatch must catch it and name it. "with_gil" means "needs the GIL": the legacy case.
 *   Its twin, without_gil.c, is identical except for one slot.
 *
 * READ FIRST
 *   labs/k4/gil_reenabler/README.md (a line-by-line walkthrough), docs/concepts/c-extensions.md
 */

/* PY_SSIZE_T_CLEAN: use Py_ssize_t (not int) for sizes in the argument parsers. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>

/* The one function this module offers: add(a, b) -> a + b for two Python ints. */
static PyObject *
add(PyObject *module, PyObject *args)
{
    long a, b;
    /* "ll" = two C longs; on a wrong argument it raises TypeError and returns 0. */
    if (!PyArg_ParseTuple(args, "ll", &a, &b)) {
        return NULL;
    }
    return PyLong_FromLong(a + b);
}

/* The module's function table, ended by an all-zero entry. */
static PyMethodDef methods[] = {
    {"add", add, METH_VARARGS, "Return a + b."},
    {NULL, NULL, 0, NULL},
};

/* Multi-phase initialization (PEP 489) slots. Deliberately NO Py_mod_gil slot here:
 * on a free-threaded build that means "I assume a GIL protects me", so CPython enables it. */
static PyModuleDef_Slot slots[] = {
    {Py_mod_multiple_interpreters, Py_MOD_PER_INTERPRETER_GIL_SUPPORTED},
    {0, NULL},
};

static struct PyModuleDef module_def = {
    PyModuleDef_HEAD_INIT,
    .m_name = "kiln_gil_labs.with_gil",
    .m_doc = "LAB: a C extension that needs the GIL (no Py_mod_gil slot).",
    .m_size = 0,
    .m_methods = methods,
    .m_slots = slots,
};

/* The entry point Python looks for: PyInit_<last part of the module name>. */
PyMODINIT_FUNC
PyInit_with_gil(void)
{
    return PyModuleDef_Init(&module_def);
}
