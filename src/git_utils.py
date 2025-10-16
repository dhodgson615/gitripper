from os import walk
from pathlib import Path
from shutil import rmtree
from subprocess import DEVNULL, run
from sys import stderr


def remove_embedded_git(dirpath: Path) -> None:
    """Recursively remove any .git directories."""
    for root, dirs, _ in walk(dirpath):
        if ".git" in dirs:
            git_dir = Path(root) / ".git"

            try:
                rmtree(git_dir)
                print(f"Removed embedded .git at {git_dir}")

            except OSError as e:
                print(
                    f"Warning: failed to remove embedded .git at "
                    f"{git_dir}: {e}",
                    file=stderr,
                )


def check_git_installed() -> None:
    """Ensure git is available in PATH."""
    try:
        run(
            ["git", "--version"],
            check=True,
            stdout=DEVNULL,
            stderr=DEVNULL,
        )
    except OSError as e:
        raise EnvironmentError(
            "git is not installed or not available in PATH."
        ) from e
