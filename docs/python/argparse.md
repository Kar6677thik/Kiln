# argparse (command-line interfaces)

> **First used in:** see the [ledger](README.md) (milestone K0) · **Needs:** [functions](functions.md), [running Python and the REPL](running-python-and-the-repl.md) · **Reading time:** ~11 min
> **Terms introduced:** **command-line argument**, **argparse**, **parser**, **positional argument**, **option**, **subcommand**, **usage error**, **exit code 2**

## What it looks like

```python
import argparse

parser = argparse.ArgumentParser(prog="toy", description="A toy CLI shaped like kiln's.")
parser.add_argument("--version", action="version", version="toy 0.1.0")
sub = parser.add_subparsers(dest="command", required=True)
greet = sub.add_parser("greet", help="say hello")
greet.add_argument("name", help="who to greet")
greet.add_argument("--times", type=int, default=1, help="how many times (default: 1)")

args = parser.parse_args(["greet", "Ada", "--times", "2"])
# args.command == "greet", args.name == "Ada", args.times == 2  (an int, not the string "2")
```

## What it means

When you type `kiln doctor --json` in a terminal, the program receives a list of strings after its own name, `["doctor", "--json"]`. These are its **command-line arguments**, available in Python as `sys.argv[1:]`. **`argparse`** is the standard-library module that turns that list of strings into named, checked values, and writes the `--help` text for you.

- `argparse.ArgumentParser(...)` creates a **parser**: an object that knows which arguments are allowed.
- `add_argument("name")`: a name without dashes is a **positional argument**. It's required, and it's recognised by its position.
- `add_argument("--times", type=int, default=1)`: a name with `--` is an **option**. It's usually optional and has a default. `type=int` converts the text `"2"` into the number `2`, and complains if it can't.
- `action="version"`: a ready-made action that prints the version and exits.
- `add_subparsers(...)` and `add_parser("greet")` create **subcommands**: words like `git commit` or `kiln doctor` that select *what to do*. Each subcommand gets its own parser with its own arguments. `dest="command"` stores which subcommand was chosen in `args.command`.
- `parse_args(list)` does the work and returns an object with one attribute per argument (`args.name`, `args.times`).
- `help="..."` strings feed the automatic `--help` output.

When the input is wrong (a missing argument, an unknown subcommand, `--times two`), argparse prints a short usage line plus an error message to stderr, and **exits with exit code 2**. Exit code 2 means "you used the command wrong", which is a **usage error**. It's a Unix convention, and Kiln's spec follows it (spec/04 §5.6: exit code **2** is bad config or usage).

## Why people use it

You could read `sys.argv` by hand, but then you'd have to write the conversion, the validation, the error messages and the help text yourself, for every option. argparse gives consistent behaviour that users already expect: `-h`/`--help` everywhere, errors that show the correct usage, and exit code 2. It's in the standard library, which matters for Kiln: there are no third-party runtime dependencies (ADR-005), so no `click` or `typer`.

## How Kiln uses it

- [`src/kiln/cli.py`](../../src/kiln/cli.py) builds Kiln's parser. In K0 it has the `--version` flag and the `doctor` subcommand (with `--json` and `--milestone Kn`). K1 and later milestones add `serve`, `submit`, `stats` and more (spec/05 §4).
- Its `main(argv) -> int` function takes the argument list as a parameter instead of reading `sys.argv` itself. That way tests can call `main(["doctor", "--json"])` directly, and the returned number becomes the process's exit code.
- [`scripts/check_docs.py`](../../scripts/check_docs.py) uses argparse for `--only`, `--milestone` and `--format`.
- Kiln's documentation standard asks for `--help` text written for beginners, with an example (spec/05 §4).

## Gotchas

- **argparse exits the program itself** on errors and on `--help`/`--version`, by raising `SystemExit`. In a test, that shows up as a `SystemExit` exception, which the test must expect (`pytest.raises(SystemExit)`).
- **Everything arrives as a string** unless you pass `type=`. Without `type=int`, `args.times` would be `"2"`, and `range("2")` fails.
- **Dashes become underscores.** An option `--max-jobs` is read as `args.max_jobs`.
- **Subcommands are optional by default.** Without `required=True`, running the program with no subcommand gives `args.command == None` instead of an error.
- **Options before or after the subcommand matter.** In `toy --version greet`, `--version` belongs to the main parser. In `toy greet --times 2`, `--times` belongs to `greet`. Kiln's global flags (spec/05 §4) go *before* the subcommand.

## Try it

The demo is [`snippets/argparse_1.py`](snippets/argparse_1.py). With no arguments at all, it shows its help. (That's a choice made in the snippet so it runs cleanly as a demo; real tools differ.)

```text
(wsl) $ python3.15 argparse_1.py --help
usage: toy [-h] [--version] {greet,doctor} ...

A toy CLI shaped like kiln's.

positional arguments:
  {greet,doctor}
    greet         say hello
    doctor        pretend to check the environment

options:
  -h, --help      show this help message and exit
  --version       show program's version number and exit
(wsl) $ python3.15 argparse_1.py greet --help
usage: toy greet [-h] [--times TIMES] name

positional arguments:
  name           who to greet

options:
  -h, --help     show this help message and exit
  --times TIMES  how many times (default: 1)
(wsl) $ python3.15 argparse_1.py greet Ada --times 2
hello, Ada
hello, Ada
(wsl) $ python3.15 argparse_1.py --version
toy 0.1.0
```

Usage errors, each with exit code 2 (`$?` holds the exit code of the last command):

```text
(wsl) $ python3.15 argparse_1.py greet; echo "exit code: $?"
usage: toy greet [-h] [--times TIMES] name
toy greet: error: the following arguments are required: name
exit code: 2
(wsl) $ python3.15 argparse_1.py greet Ada --times two; echo "exit code: $?"
usage: toy greet [-h] [--times TIMES] name
toy greet: error: argument --times: invalid int value: 'two'
exit code: 2
(wsl) $ python3.15 argparse_1.py dance; echo "exit code: $?"
usage: toy [-h] [--version] {greet,doctor} ...
toy: error: argument command: invalid choice: 'dance' (choose from 'greet', 'doctor')
exit code: 2
```

(These were run from inside `docs/python/snippets/`.)

## Check yourself

1. What is the difference between `add_argument("name")` and `add_argument("--name")`?
   <details><summary>Answer</summary>

   The first is a positional argument: required, matched by position (`toy greet Ada`). The second is an option: optional, given by name (`--name Ada`), and `None` or its default if left out.

   </details>
2. A user types `kiln doctor --milestone`, forgetting the value. What exit code will they see, and why that number?
   <details><summary>Answer</summary>

   2. argparse reports the usage error and exits with 2, the Unix convention for "command used incorrectly", which Kiln's spec adopts.

   </details>
3. Why does Kiln's `main` take `argv` as a parameter instead of reading `sys.argv`?
   <details><summary>Answer</summary>

   So tests can call `main(["doctor", "--json"])` with any arguments they like, and check the returned exit code, without starting a new process.

   </details>
