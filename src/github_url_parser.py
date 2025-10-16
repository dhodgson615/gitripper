from re import match
from typing import Tuple


def parse_github_url(url: str) -> Tuple[str, str]:
    """Parse GitHub URL and return (owner, repo), or raise ValueError on failure."""
    result = next(
        (
            (m.group(1), m.group(2))
            for m in [
                match(r, url.strip().removesuffix(".git"))
                for r in [
                    r"https?://github\.com/([^/]+)/([^/]+)(/.*)?$",
                    r"git@github\.com:([^/]+)/([^/]+)$",
                    r"ssh://git@github\.com/([^/]+)/([^/]+)$",
                ]
            ]
            if m
        ),
        None,
    )
    if not result or not all(result):
        raise ValueError(f"Invalid GitHub URL: {url}")
    return result
