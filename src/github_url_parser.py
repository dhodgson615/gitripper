from re import match
from typing import Tuple


def parse_github_url(url: str) -> Tuple[str, str]:
    """Parse GitHub URL and return (owner, repo), or raise ValueError
    on failure.
    """
    url = url.strip().removesuffix(".git")

    for r in [
        r"https?://github\.com/([^/]+)/([^/]+)(/.*)?$",
        r"git@github\.com:([^/]+)/([^/]+)$",
        r"ssh://git@github\.com/([^/]+)/([^/]+)$",
    ]:
        m = match(r, url)

        if m:
            return m.group(1), m.group(2)

    raise ValueError(f"Invalid GitHub URL: {url}")
