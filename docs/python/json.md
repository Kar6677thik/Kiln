# json (Python values ↔ JSON text)

> **First used in:** see the [ledger](README.md) (milestone K0) · **Needs:** [variables-and-types](variables-and-types.md), [bytes-vs-str](bytes-vs-str.md) · **Reading time:** ~7 min
> **Terms introduced:** **serialize**, **deserialize**, **JSON**, **JSONDecodeError**

## What it looks like

```python
import json

text = json.dumps({"type": "submit", "args": [2, 3]})   # Python dict → JSON text
message = json.loads(text)                               # JSON text → Python dict
```

## What it means

**JSON** is a text format for data: objects `{}`, arrays `[]`, strings, numbers, `true`/`false`/`null`. The `json` module translates:

- `json.dumps(value)` ("dump to string") **serializes**: it turns a Python value into JSON text.
- `json.loads(text)` ("load from string") **deserializes**: it turns JSON text back into Python values.

| Python | JSON | Back into Python |
|---|---|---|
| `dict` | object `{}` | `dict` |
| `list`, **`tuple`** | array `[]` | **`list`** |
| `str` | string | `str` |
| `int`, `float` | number | `int` or `float` |
| `True` / `False` / `None` | `true` / `false` / `null` | `True` / `False` / `None` |

Options Kiln uses (spec/02 §2):

| Option | Effect | Why Kiln sets it |
|---|---|---|
| `separators=(",", ":")` | no spaces after `,` and `:` | smaller frames |
| `ensure_ascii=False` | write `é` as itself instead of `é` | readable, and shorter. The text is UTF-8 encoded afterwards anyway |
| `allow_nan=False` | refuse `NaN` and `Infinity` | they aren't valid JSON, and other JSON parsers reject them |

## Why people use it

JSON is the common language of web APIs and logs: every programming language can read it, and humans can too. Kiln uses JSON (not `pickle`) for job arguments and results because it can't run code when loaded, and doesn't depend on the Python version ([ADR-009](../decisions.md#adr-009)).

## How Kiln uses it

- [`src/kiln/protocol/framing.py`](../../src/kiln/protocol/framing.py) encodes every KP/1 message with `json.dumps(obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False)` and decodes with `json.loads(..., parse_constant=...)`, where `parse_constant` rejects `NaN`/`Infinity` on the way in.
- [`src/kiln/bench/records.py`](../../src/kiln/bench/records.py) writes ExperimentRecords with `json.dumps(record, indent=2, sort_keys=True, allow_nan=False)`: pretty, stably ordered, and strict.

## Gotchas

- **Tuples come back as lists.** `json.dumps((1, 2))` is `'[1, 2]'`, and loading it gives `[1, 2]`. A task receiving `args` gets lists, never tuples (spec/03 §2.1).
- **Dict keys become strings.** `{1: "one"}` round-trips to `{'1': 'one'}`.
- **`json.dumps` accepts NaN by default and writes `NaN`**, which isn't JSON. Python's `json.loads("NaN")` also accepts it by default. Kiln turns both off, so a broken measurement or message fails loudly instead of confusing another program.
- **Sets, bytes and datetimes aren't serializable.** You get `TypeError: Object of type set is not JSON serializable`.
- **Broken text raises `json.JSONDecodeError`** (a subclass of `ValueError`), with the position of the problem. Kiln maps it to the error code `BAD_FRAME`.

## Try it

```text
>>> import json
>>> json.dumps({"type": "submit", "args": [2, 3]})
'{"type": "submit", "args": [2, 3]}'
>>> json.dumps({"type": "submit", "args": [2, 3]}, separators=(",", ":"))
'{"type":"submit","args":[2,3]}'
>>> json.dumps({"name": "café"})
'{"name": "caf\\u00e9"}'
>>> json.dumps({"name": "café"}, ensure_ascii=False)
'{"name": "café"}'
>>> json.dumps((1, 2))
'[1, 2]'
>>> json.loads(json.dumps({1: "one"}))
{'1': 'one'}
>>> json.loads('{"ok": true, "value": null}')
{'ok': True, 'value': None}
>>> json.dumps(float("nan"))
'NaN'
>>> json.dumps(float("nan"), allow_nan=False)
Traceback (most recent call last):
  File "<console>", line 1, in <module>
  …
ValueError: Out of range float values are not JSON compliant: nan
>>> json.loads("NaN")
nan
>>> json.dumps({1, 2})
Traceback (most recent call last):
  File "<console>", line 1, in <module>
  …
TypeError: Object of type set is not JSON serializable
>>> json.loads("[1, 2")
Traceback (most recent call last):
  File "<console>", line 1, in <module>
  …
json.decoder.JSONDecodeError: Expecting ',' delimiter: line 1 column 6 (char 5)
```

(Generated from [`snippets/json.repl`](snippets/json.repl) on Python 3.15.1. The "…" lines replace frames inside the `json` package itself.)

## Check yourself

1. What does `json.loads(json.dumps({"args": (1, 2)}))` return?
   <details><summary>Answer</summary>

   `{'args': [1, 2]}`. The tuple became a JSON array, which loads back as a list.

   </details>
2. Why does Kiln pass `allow_nan=False`?
   <details><summary>Answer</summary>

   `NaN` and `Infinity` aren't part of the JSON standard. Python writes them by default, but other programs would reject the message or record. Refusing them makes the error appear where it's caused.

   </details>
3. A client sends the frame text `{"type": "ping"` (missing `}`). What happens in Kiln?
   <details><summary>Answer</summary>

   `json.loads` raises `JSONDecodeError`. The decoder reports it as a bad frame, and the master replies `BAD_FRAME` and closes the connection (spec/02 §2.1).

   </details>
