# A decorator WITH arguments that registers functions in a table,
# the same shape as Kiln's @app.task (K1), plus functools.wraps.
import functools

TASKS = {}


def task(name=None):
    # task(...) is called first, with the options. It returns the real decorator.
    def decorator(func):
        TASKS[name or func.__name__] = func
        return func  # registration only: hand back the original function unchanged

    return decorator


@task()
def add(a, b):
    return a + b


@task(name="img.resize")
def resize(path, width):
    return f"resized {path} to {width}px"


print("registered:", sorted(TASKS))
print("add(2, 3) =", add(2, 3))
print("lookup by name:", TASKS["img.resize"]("cat.png", 100))


def logged(func):
    @functools.wraps(func)  # copy func's name and docstring onto wrapper
    def wrapper(*args, **kwargs):
        print(f"calling {func.__name__} with {args}")
        result = func(*args, **kwargs)
        print(f"{func.__name__} returned {result}")
        return result

    return wrapper


@logged
def double(x):
    """Return x times two."""
    return x * 2


double(4)
print("name:", double.__name__, "| doc:", double.__doc__)
