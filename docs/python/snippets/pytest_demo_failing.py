# The same idea with a planted bug, so you can see what a failure report looks like.
# Named pytest_demo_*.py so the repo's own test run never collects it.
def add(a, b):
    return a - b  # BUG: should be a + b


def test_add_small_numbers():
    total = add(2, 3)
    assert total == 5
