from __future__ import annotations

from argparse import ArgumentParser
from os import environ, walk
from pathlib import Path
from re import match
from shutil import move, rmtree
from subprocess import DEVNULL, run
from sys import exit, stderr
from tempfile import TemporaryDirectory
from typing import Optional, Tuple

try:
    import requests

except ImportError:
    print("Package 'requests' not found. Attempting to install...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests


GITHUB_API = "https://api.github.com"


def parse_github_url(url: str) -> Tuple[str, str]:
    """Parse GitHub URL and return (owner, repo)."""
    url = url.strip()

    if url.endswith(".git"):
        url = url[:-4]

    # HTTPS or HTTP
    if m := re.match(r"https?://github\.com/([^/]+)/([^/]+)(/.*)?$", url):
        return m.group(1), m.group(2)

    # SSH form: git@github.com:owner/repo
    if m := re.match(r"git@github\.com:([^/]+)/([^/]+)$", url):
        return m.group(1), m.group(2)

    # ssh://git@github.com/owner/repo
    if m := re.match(r"ssh://git@github\.com/([^/]+)/([^/]+)$", url):
        return m.group(1), m.group(2)

    raise ValueError(f"Could not parse GitHub URL: {url}")


def get_default_branch(owner: str, repo: str, token: Optional[str]) -> str:
    """Query GitHub API for default_branch."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}"
    headers = {"Authorization": f"token {token}"} if token else {}
    r = requests.get(url, headers=headers, timeout=30)

    if r.status_code == 200:
        branch_name: str = r.json().get("default_branch", "main")
        return branch_name

    elif r.status_code == 404:
        raise FileNotFoundError(f"Repository {owner}/{repo} not found (404).")

    else:
        raise RuntimeError(
            f"Failed to get repo info: {r.status_code} {r.text}"
        )


def download_zip(
    owner: str, repo: str, ref: str, token: Optional[str], dest_path: Path
) -> Path:
    """Download the zip archive for owner/repo@ref."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/zipball/{ref}"
    headers = {"Accept": "application/vnd.github+json"}

    if token:
        headers["Authorization"] = f"token {token}"

    with requests.get(url, headers=headers, stream=True, timeout=60) as r:
        if r.status_code == 200:
            zip_file = dest_path / f"{repo}-{ref}.zip"

            with open(zip_file, "wb") as fh:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        fh.write(chunk)

            return zip_file

        elif r.status_code in (301, 302):
            raise RuntimeError(f"Unexpected redirect: {r.status_code}")

        elif r.status_code == 404:
            raise FileNotFoundError(
                f"Archive for {owner}/{repo}@{ref} not found (404)."
            )

        else:
            raise RuntimeError(
                f"Failed to download archive: {r.status_code} {r.text}"
            )


def extract_zip(zip_path: Path, dest_dir: Path) -> None:
    """Extract zip archive to destination directory."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        if not zf.namelist():
            raise RuntimeError("Zip archive is empty.")

        with tempfile.TemporaryDirectory() as tmpd:
            temp_path = Path(tmpd)
            zf.extractall(path=temp_path)
            top_level_dirs = [p for p in temp_path.iterdir() if p.is_dir()]

            if not dest_dir.exists():
                dest_dir.mkdir(parents=True)

            # Extract content from temp dir to destination
            source_dir = (
                top_level_dirs[0] if len(top_level_dirs) == 1 else temp_path
            )

            for item in source_dir.iterdir():
                target = dest_dir / item.name

                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)

                    else:
                        target.unlink()

                shutil.move(str(item), str(target))


def remove_embedded_git(dirpath: Path) -> None:
    """Recursively remove any .git directories."""
    for root, dirs, _ in os.walk(dirpath):
        if ".git" in dirs:
            git_dir = Path(root) / ".git"

            try:
                shutil.rmtree(git_dir)
                print(f"Removed embedded .git at {git_dir}")

            except Exception as e:
                print(
                    f"Warning: failed to remove embedded .git at "
                    f"{git_dir}: {e}",
                    file=sys.stderr,
                )


def check_git_installed() -> None:
    """Ensure git is available in PATH."""
    try:
        subprocess.run(
            ["git", "--version"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    except Exception as e:
        raise EnvironmentError(
            "git is not installed or not available in PATH."
        ) from e


def initialize_repo(
    dest: Path,
    author_name: Optional[str],
    author_email: Optional[str],
    remote: Optional[str],
) -> None:
    """Initialize git repo with initial commit."""
    subprocess.run(["git", "init"], cwd=str(dest), check=True)

    if author_name:
        subprocess.run(
            ["git", "config", "user.name", author_name],
            cwd=str(dest),
            check=True,
        )

    if author_email:
        subprocess.run(
            ["git", "config", "user.email", author_email],
            cwd=str(dest),
            check=True,
        )

    subprocess.run(["git", "add", "."], cwd=str(dest), check=True)

    subprocess.run(
        ["git", "commit", "-m", "Initial commit"], cwd=str(dest), check=True
    )

    if remote:
        subprocess.run(
            ["git", "remote", "add", "origin", remote],
            cwd=str(dest),
            check=True,
        )

        print(f"Set remote origin to {remote}")


def main() -> None:
    """Main entry point."""
    p = argparse.ArgumentParser(
        description="Download a GitHub repository's contents and "
        "create a local git repo."
    )

    p.add_argument("url", help="URL of the GitHub repository")

    p.add_argument(
        "--branch",
        help="Branch/ref to fetch (default: repo default branch)",
        default=None,
    )

    p.add_argument(
        "--token", help="GitHub personal access token", default=None
    )

    p.add_argument(
        "--dest",
        help="Destination directory (default: ./<repo>-copy)",
        default=None,
    )

    p.add_argument(
        "--author-name",
        help="Set git user.name for the initial commit",
        default=None,
    )

    p.add_argument(
        "--author-email",
        help="Set git user.email for the initial commit",
        default=None,
    )

    p.add_argument(
        "--remote",
        help="Set git remote origin after initial commit",
        default=None,
    )

    p.add_argument(
        "--force",
        help="Overwrite destination if it exists",
        action="store_true",
    )

    args = p.parse_args()
    token = args.token or os.environ.get("GITHUB_TOKEN")

    try:
        owner, repo = parse_github_url(args.url)

    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)

    dest = Path(args.dest) if args.dest else Path(f"{repo}-copy").resolve()

    # Check if destination exists
    if dest.exists():
        if any(dest.iterdir()) and not args.force:
            print(
                f"Destination '{dest}' exists and is not empty. "
                f"Use --force to overwrite.",
                file=sys.stderr,
            )

            sys.exit(3)

        elif args.force:
            try:
                if dest.is_dir():
                    shutil.rmtree(dest)

                else:
                    dest.unlink()

            except Exception as e:
                print(
                    f"Failed to remove existing destination: {e}",
                    file=sys.stderr,
                )

                sys.exit(4)

    try:
        check_git_installed()

    except EnvironmentError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(5)

    # Determine ref
    ref = args.branch
    if ref is None:
        try:
            ref = get_default_branch(owner, repo, token)
            print(f"Using default branch '{ref}'")

        except Exception as e:
            print(
                f"Warning: could not determine default branch: {e}. "
                f"Using 'main'."
            )

            ref = "main"

    # Download archive and extract
    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir_path = Path(tmp_dir_str)

        try:
            print(f"Downloading {owner}/{repo}@{ref} ...")
            zip_path = download_zip(owner, repo, ref, token, tmp_dir_path)
            print(f"Downloaded archive to {zip_path}")

        except Exception as e:
            print(
                f"Failed to download repository archive: {e}", file=sys.stderr
            )

            sys.exit(6)

        try:
            print(f"Extracting archive to {dest} ...")
            extract_zip(zip_path, dest)

        except Exception as e:
            print(f"Failed to extract archive: {e}", file=sys.stderr)
            sys.exit(7)

    # Remove any embedded .git and initialize repo
    remove_embedded_git(dest)

    try:
        print("Initializing new git repository...")
        initialize_repo(dest, args.author_name, args.author_email, args.remote)

    except Exception as e:
        print(f"Failed to initialize repository: {e}", file=sys.stderr)
        sys.exit(8)

    print("Done. Repository copied to:", dest)
    print("Note: this repository has no history from the original repo.")


if __name__ == "__main__":
    main()
