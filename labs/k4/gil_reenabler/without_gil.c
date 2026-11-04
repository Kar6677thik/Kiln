/*
 * kiln_gil_labs.without_gil: the same tiny C extension, DECLARING free-threading support.
 *
 * WHY THIS FILE EXISTS
 *   The control for with_gil.c: identical code plus one slot, {Py_mod_gil, Py_MOD_GIL_NOT_USED},
 *   which promises "I'm safe without the GIL". Importing it on 3.15t must leave the GIL off.
 *   "without_gil" means "runs without the GIL".
 *
 * READ FIRST
 *   labs/k4/gil_reenabler/README.md, docs/concepts/c-extensions.md
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

static PyObject *
add(PyObject *module, PyObject *args)
{
    long a, b;
    if (!PyArg_ParseTuple(args, "ll", &a, &b)) {
        return NULL;
    }
    return PyLong_FromLong(a + b);
}

static PyMethodDef methods[] = {
    {"add", add, METH_VARARGS, "Return a + b."},
    {NULL, NULL, 0, NULL},
};

static PyModuleDef_Slot slots[] = {
    {Py_mod_multiple_interpreters, Py_MOD_PER_INTERPRETER_GIL_SUPPORTED},
    /* THE difference: this module touches no shared state without its own locking, so it
     * declares that the GIL may stay off. On a GIL build this slot is simply ignored. */
    {Py_mod_gil, Py_MOD_GIL_NOT_USED},
    {0, NULL},
};

static struct PyModuleDef module_def = {
    PyModuleDef_HEAD_INIT,
    .m_name = "kiln_gil_labs.without_gil",
    .m_doc = "A C extension that declares Py_mod_gil = Py_MOD_GIL_NOT_USED.",
    .m_size = 0,
    .m_methods = methods,
    .m_slots = slots,
};

PyMODINIT_FUNC
PyInit_without_gil(void)
{
    return PyModuleDef_Init(&module_def);
}
