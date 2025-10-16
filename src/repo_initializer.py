from pathlib import Path
from subprocess import run
from typing import Optional


def initialize_repo(
    dest: Path,
    author_name: Optional[str],
    author_email: Optional[str],
    remote: Optional[str],
) -> None:
    """Initialize git repo with initial commit."""

    def git(*args: str) -> None:
        run(["git", *args], cwd=str(dest), check=True)

    git("init")
    for key, value in [
        ("user.name", author_name),
        ("user.email", author_email),
    ]:
        if value:
            git("config", key, value)
    git("add", ".")
    git("commit", "-m", "Initial commit")
    if remote:
        run(
            ["git", "remote", "add", "origin", remote],
            cwd=str(dest),
            check=True,
        )

        print(f"Set remote origin to {remote}")
