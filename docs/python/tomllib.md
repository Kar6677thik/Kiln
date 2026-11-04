# `tomllib` (reading TOML files)

> **First used in:** see the [ledger](README.md) (milestone K1) · **Needs:** [variables-and-types](variables-and-types.md), [context-managers](context-managers.md), [exceptions](exceptions.md) · **Reading time:** ~6 min
> **Terms introduced:** **tomllib**, **TOML table**, **TOMLDecodeError**, **binary mode ("rb")**

## What it looks like

```python
import tomllib

with open("kiln.toml", "rb") as file:      # note "rb": read, binary
    data = tomllib.load(file)
workers = data["master"]["workers"]        # 4
```

## What it means

**TOML** is a config file format: `key = value` lines grouped under `[section]` headers, which TOML calls **TOML tables**. The standard-library module **tomllib** (Python 3.11+) reads TOML into ordinary Python values:

| TOML | Becomes in Python |
|---|---|
| `[master]` table | a `dict` |
| `workers = 4` | `int` |
| `heartbeat_interval_s = 1.0` | `float` |
| `bind = "127.0.0.1:7340"` | `str` |
| `preload = true` | `bool` (`True`) |
| `pythonpath = ["src", "examples"]` | `list` |
| `started = 2026-11-04T10:00:00Z` | `datetime.datetime` |

- `tomllib.load(file)` reads an open file, which must be opened in **binary mode ("rb")**.
- `tomllib.loads(text)` parses a string.
- Invalid TOML raises **TOMLDecodeError**, with the problem and its position.

`tomllib` only *reads*; there's no writer in the standard library.

## Why people use it

TOML is designed to be easy for people to read and edit, with real types: `4` is a number and `true` is a boolean, not strings that need guessing. It's the format of `pyproject.toml`, which is why Python added a reader to the standard library. Kiln uses it for `kiln.toml` too, which keeps one config format across the project.

## How Kiln uses it

[`src/kiln/config.py`](../../src/kiln/config.py) reads `kiln.toml` with `tomllib.load` (spec/05 §1).

- **Env vars as TOML:** it also parses environment-variable values *as TOML values*, so `KILN_MASTER__WORKERS=8` becomes the int `8`, and `KILN_LABS__ENABLED=true` becomes `True`. It parses `"x = " + value` with `tomllib.loads` and keeps the raw string if that fails.
- **Validation comes after:** TOML only knows types, not Kiln's rules. `workers = "four"` is perfectly valid TOML that gives a string. Rejecting it is `config.py`'s validation step, which collects every problem and exits with code 2.

## Gotchas

- **Binary mode is required.** `tomllib.load()` on a file opened in text mode (`"r"`) raises `TypeError`. TOML files are always UTF-8, and the parser does the decoding itself (see the snippet below).
- **Valid TOML can still be a wrong config.** The parser checks syntax and gives you types; it doesn't know that `workers` must be a positive integer. Validation is your job.
- **Tables are plain dicts.** A misspelled section (`[mastr]`) parses without complaint into a new key. That's why Kiln treats unknown keys as errors (spec/05 §1).

## Try it

```text
>>> import tomllib
>>> text = """
... [master]
... workers = 4
... bind = "127.0.0.1:7340"
... 
... [worker]
... max_rss_mb = 512
... heartbeat_interval_s = 1.0
... 
... [app]
... pythonpath = ["src", "examples"]
... preload = true
... """
>>> config = tomllib.loads(text)
>>> config
{'master': {'workers': 4, 'bind': '127.0.0.1:7340'}, 'worker': {'max_rss_mb': 512, 'heartbeat_interval_s': 1.0}, 'app': {'pythonpath': ['src', 'examples'], 'preload': True}}
>>> type(config["worker"]["heartbeat_interval_s"])
<class 'float'>
>>> config["app"]["preload"]
True
>>> tomllib.loads('workers = "four"')["workers"]
'four'
>>> tomllib.loads("workers = ")
Traceback (most recent call last):
  File "<console>", line 1, in <module>
  …(frames inside tomllib/_parser.py trimmed)…
tomllib.TOMLDecodeError: Invalid value (at end of document)
```

(Generated from [`snippets/tomllib.repl`](snippets/tomllib.repl) on Python 3.15.1. The real traceback lists five frames inside the parser; read it from the bottom.) `true` became `True`, and `1.0` became a `float`. `"four"` parsed fine, because only Kiln's validation can reject it.

Binary vs text mode, from [`snippets/tomllib_1.py`](snippets/tomllib_1.py):

```text
binary mode: {'master': {'workers': 4}}
text mode: TypeError - File must be opened in binary mode, e.g. use `open('foo.toml', 'rb')`
```

## Check yourself

1. What Python value does `max_rss_mb = 512` become? And `preload = true`?
   <details><summary>Answer</summary>

   `512` becomes the `int` 512. `true` becomes the `bool` `True`: TOML has real booleans, written in lowercase.

   </details>
2. `tomllib.load(open("kiln.toml"))` raises `TypeError`. Why, and what's the fix?
   <details><summary>Answer</summary>

   `open()` defaults to text mode, but `tomllib.load` needs a binary file. Use `open("kiln.toml", "rb")`, ideally in a `with` block.

   </details>
3. Does `tomllib` reject `workers = -3`?
   <details><summary>Answer</summary>

   No. `-3` is a valid TOML integer. Checking that `workers ≥ 1` is the job of `config.py`'s validation, which reports it as a config error.

   </details>
