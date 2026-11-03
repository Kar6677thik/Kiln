# A tiny pytest file. Run it with pytest; running it as a script does nothing visible.
# It is named pytest_demo_*.py (not test_*.py) so the repo's own test run never collects it.
import pytest


def add(a, b):
    return a + b


def test_add_small_numbers():
    assert add(2, 3) == 5


@pytest.mark.parametrize(("a", "b", "expected"), [(1, 1, 2), (0, 0, 0), (-1, 1, 0)])
def test_add_table(a, b, expected):
    assert add(a, b) == expected


def test_writes_a_file(tmp_path):
    target = tmp_path / "out.txt"
    target.write_text("hi", encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "hi"


@pytest.fixture
def numbers():
    return [3, 1, 2]


def test_sorted_uses_fixture(numbers):
    assert sorted(numbers) == [1, 2, 3]
