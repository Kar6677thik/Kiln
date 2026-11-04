# Call a @given test directly: Hypothesis runs it on many generated inputs.
import json
import sys

try:
    from hypothesis import given
    from hypothesis import strategies as st
except ImportError:
    print("hypothesis is not installed for this interpreter; nothing to demonstrate")
    sys.exit(0)

seen = []


@given(st.dictionaries(st.text(), st.integers()))
def roundtrip(data):
    seen.append(data)
    assert json.loads(json.dumps(data)) == data


roundtrip()
print("property held; examples tried:", len(seen) >= 50)
print("an empty dict was among them:", {} in seen)
