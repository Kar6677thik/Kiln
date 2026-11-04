# A pytest file showing Hypothesis. Run it with pytest; as a plain script it does nothing.
# Named *_example.py (not test_*.py) so Kiln's own test run never collects it.
import sys

try:
    from hypothesis import given, settings
    from hypothesis import strategies as st
    from hypothesis.stateful import RuleBasedStateMachine, invariant, rule
except ImportError:
    print("hypothesis is not installed for this interpreter; nothing to demonstrate")
    sys.exit(0)

from collections import deque


def mean(numbers):
    return sum(numbers) / len(numbers)  # BUG: crashes on an empty list


@given(st.lists(st.integers()))
@settings(derandomize=True)
def test_mean_is_between_min_and_max(numbers):
    """For any list of integers, the mean lies between the smallest and largest value."""
    assert min(numbers) <= mean(numbers) <= max(numbers)


@given(st.lists(st.integers(), min_size=1))
@settings(derandomize=True)
def test_mean_of_non_empty_lists(numbers):
    """The same property, now only for lists with at least one item."""
    assert min(numbers) <= mean(numbers) <= max(numbers)


class QueueMachine(RuleBasedStateMachine):
    """Drive a real deque and a plain-list model side by side; they must always agree."""

    def __init__(self):
        super().__init__()
        self.real = deque()
        self.model = []

    @rule(item=st.integers())
    def push(self, item):
        self.real.append(item)
        self.model.append(item)

    @rule()
    def pop(self):
        if self.model:
            assert self.real.popleft() == self.model.pop(0)

    @invariant()
    def same_contents(self):
        assert list(self.real) == self.model


TestQueue = QueueMachine.TestCase

if __name__ == "__main__":
    print("run me with pytest: pytest hypothesis_demo_example.py")
