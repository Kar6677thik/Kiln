# Kiln

**A pre-fork job server built from scratch to learn how Python really works.**

Kiln is a small server in the style of gunicorn and Celery. A *master* process supervises a team of *worker* processes and hands them jobs from an in-memory queue. Each milestone recreates a real production incident (Instagram's copy-on-write memory problem, the Python 3.14 GC revert, forkserver breakages, blocked event loops, memory leaks, free-threading), measures it, fixes it, and explains it.

> **Status:** K0 (foundations) built and awaiting review (2026-11-03). Next: K1, the master/worker supervisor.
> **This is a learning project.** Correctness and explanation come before features. It isn't meant for production use: there's no persistence, no auth, and it's localhost only.

## Quick start

From a fresh WSL (Debian) terminal:

```bash
cd "/mnt/c/Users/Karthik Reddy/Documents/Study/Projects/Kiln"   # the quotes matter: the path has a space
./scripts/dev.sh doctor      # what does this machine still need? (each ⚠️/❌ line shows its fix)
./scripts/dev.sh check       # lint + type check + tests + documentation rules: should end "all green"
```

New to the terminal? Read [`docs/concepts/terminal-and-wsl.md`](docs/concepts/terminal-and-wsl.md) first.

## Start here
- New to all of this? → [`docs/README.md`](docs/README.md) gives the reading order.
- What is being built? → [`docs/spec/00-overview.md`](docs/spec/00-overview.md)
- In what order? → [`docs/milestones/README.md`](docs/milestones/README.md)
- Why this way? → [`docs/decisions.md`](docs/decisions.md)
- Where did the ideas come from? → [`docs/research/python-mastery-research.md`](docs/research/python-mastery-research.md) (+ [errata](docs/research/errata.md))

## Runs on
WSL2 Debian 13, with CPython 3.13 / 3.14 / 3.15 / 3.15t managed by `uv`. See [`docs/spec/07-environment.md`](docs/spec/07-environment.md).
