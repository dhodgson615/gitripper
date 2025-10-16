from __future__ import annotations

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
    run(["git", "init"], cwd=str(dest), check=True)

    if author_name:
        run(
            ["git", "config", "user.name", author_name],
            cwd=str(dest),
            check=True,
        )

    if author_email:
        run(
            ["git", "config", "user.email", author_email],
            cwd=str(dest),
            check=True,
        )

    run(["git", "add", "."], cwd=str(dest), check=True)
    run(["git", "commit", "-m", "Initial commit"], cwd=str(dest), check=True)

    if remote:
        run(
            ["git", "remote", "add", "origin", remote],
            cwd=str(dest),
            check=True,
        )

        print(f"Set remote origin to {remote}")
