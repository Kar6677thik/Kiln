# Kiln

**A pre-fork job server built from scratch to learn how Python really works.**

Kiln is a small server in the style of gunicorn and Celery. A *master* process supervises a team of *worker* processes and hands them jobs from an in-memory queue. Each milestone recreates a real production incident (Instagram's copy-on-write memory problem, the Python 3.14 GC revert, forkserver breakages, blocked event loops, memory leaks, free-threading), measures it, fixes it, and explains it.

> **Status:** spec pass complete (2026-11-03). No code yet. Implementation starts with milestone **K0**.
> **This is a learning project.** Correctness and explanation come before features. It isn't meant for production use: there's no persistence, no auth, and it's localhost only.

## Start here
- New to all of this? → [`docs/README.md`](docs/README.md) gives the reading order.
- What is being built? → [`docs/spec/00-overview.md`](docs/spec/00-overview.md)
- In what order? → [`docs/milestones/README.md`](docs/milestones/README.md)
- Why this way? → [`docs/decisions.md`](docs/decisions.md)
- Where did the ideas come from? → [`docs/research/python-mastery-research.md`](docs/research/python-mastery-research.md) (+ [errata](docs/research/errata.md))

## Runs on
WSL2 Debian 13, with CPython 3.13 / 3.14 / 3.15 / 3.15t managed by `uv`. See [`docs/spec/07-environment.md`](docs/spec/07-environment.md).
