from __future__ import annotations

from typing import Any, Optional

from requests import get

from src.github_api import GITHUB_API


def get_default_branch(
    owner: Optional[str], repo: str, token: Optional[str]
) -> str:
    """Query GitHub API for default_branch."""
    if owner is None:
        raise ValueError("Owner cannot be None")

    url = f"{GITHUB_API}/repos/{owner}/{repo}"
    headers = {"Authorization": f"token {token}"} if token else {}
    r = get(url, headers=headers, timeout=30)

    if r.status_code == 200:
        return r.json().get("default_branch", "main")

    if r.status_code == 404:
        raise FileNotFoundError(f"Repository {owner}/{repo} not found (404).")

    raise RuntimeError(f"Failed to get repo info: {r.status_code} {r.text}")
