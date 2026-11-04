"""git-repo test_subcmds_forall, distilled: a class-level mock that children don't see.

Pattern preserved (errata E-08, commit fbc9c79): the test patches a *class* method with
mock.patch.object so it doesn't run git, then a worker Pool calls that method on projects
passed to it. Under fork the children inherit the patched class. Under forkserver/spawn
they import a fresh, unpatched class, and the real method runs (and fails).
"""

# ruff: noqa: T201  # WHY: printing is the observable behaviour this corpus case needs

import multiprocessing
from unittest import mock


class Project:
    """Stand-in for git-repo's project.Project."""

    def __init__(self, name: str) -> None:
        """Remember the project's name.

        Args:
            name: the project name.
        """
        self.name = name

    def get_revision_id(self) -> str:
        """Ask git for the revision (impossible in this test environment).

        Returns:
            Never returns here.

        Raises:
            RuntimeError: always; the real one would run `git rev-parse`.
        """
        raise RuntimeError(f"git not available for {self.name}")


def _work(project: Project) -> str:
    """The pool worker: report the project's revision.

    Args:
        project: a project (pickled into the worker).

    Returns:
        "name=revision", or the error text.
    """
    try:
        return f"{project.name}={project.get_revision_id()}"
    except RuntimeError as error:
        return f"{project.name}: {error}"


def test_forall() -> None:
    """Run the pool with the class method patched in the parent."""
    projects = [Project("platform/a"), Project("platform/b")]
    with (
        mock.patch.object(Project, "get_revision_id", return_value="refs/heads/main"),
        multiprocessing.Pool(2) as pool,
    ):
        print(pool.map(_work, projects))


if __name__ == "__main__":
    test_forall()
