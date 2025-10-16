from argparse import ArgumentParser
from os import environ
from pathlib import Path
from shutil import rmtree
from sys import exit, stderr
from tempfile import TemporaryDirectory
from typing import Any, List, Tuple

from requests import RequestException

from src.default_branch_getter import get_default_branch
from src.git_utils import check_git_installed, remove_embedded_git
from src.github_url_parser import parse_github_url
from src.repo_initializer import initialize_repo
from src.zip_utils import download_zip, extract_zip


def main() -> None:
    """Main entry point."""
    p = ArgumentParser(
        description="Download a GitHub repository's contents and create a "
        "local git repo."
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
    token = args.token or environ.get("GITHUB_TOKEN")

    try:
        owner, repo = parse_github_url(args.url)

        if owner is None or repo is None:
            raise ValueError(
                f"Could not determine repository "
                f"{'owner' if not owner else 'name' if not repo else 'info'}."
            )

    except ValueError as e:
        print(f"Error: {e}", file=stderr)
        exit(2)
        owner, repo = None, None

    dest = (
        Path(args.dest)
        if args.dest
        else Path(
            f"{repo}-copy" if repo is not None else "repo-copy"
        ).resolve()
    )

    # Check if destination exists
    if dest.exists():
        if any(dest.iterdir()) and not args.force:
            print(
                f"Destination '{dest}' exists and is not empty. "
                f"Use --force to overwrite.",
                file=stderr,
            )

            exit(3)

        elif args.force:
            try:
                rmtree(dest) if dest.is_dir() else dest.unlink()

            except OSError as e:
                print(
                    f"Failed to remove existing destination: {e}",
                    file=stderr,
                )

                exit(4)

    try:
        check_git_installed()

    except EnvironmentError as e:
        print(f"Error: {e}", file=stderr)
        exit(5)

    ref = args.branch

    if ref is None:
        try:
            ref = get_default_branch(owner, repo, token)
            print(f"Using default branch '{ref}'")

        except Exception as e:  # FIXME: specify Exception type
            print(
                f"Warning: could not determine default branch: {e}. "
                f"Using 'main'."
            )

            ref = "main"

    # Download archive and extract
    with TemporaryDirectory() as tmp_dir_str:
        tmp_dir_path = Path(tmp_dir_str)

        try:
            print(f"Downloading {owner}/{repo}@{ref} ...")
            zip_path = download_zip(owner, repo, ref, token, tmp_dir_path)
            print(f"Downloaded archive to {zip_path}")

        except RequestException as e:
            print(f"Failed to download repository archive: {e}", file=stderr)
            exit(6)

        try:
            print(f"Extracting archive to {dest} ...")
            extract_zip(zip_path, dest)

        except OSError as e:
            print(f"Failed to extract archive: {e}", file=stderr)
            exit(7)

    remove_embedded_git(dest)

    try:
        print("Initializing new git repository...")
        initialize_repo(dest, args.author_name, args.author_email, args.remote)

    except OSError as e:
        print(f"Failed to initialize repository: {e}", file=stderr)
        exit(8)

    print("Done. Repository copied to:", dest)
    print("Note: this repository has no history from the original repo.")


if __name__ == "__main__":
    main()
