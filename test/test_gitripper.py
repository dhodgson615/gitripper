from importlib import reload
from pathlib import Path
from unittest.mock import MagicMock, call, patch
from zipfile import ZipFile

from pytest import mark, raises
from requests import RequestException

from src.default_branch_getter import get_default_branch
from src.git_utils import check_git_installed, remove_embedded_git
from src.github_url_parser import parse_github_url
from src.gitripper import main
from src.repo_initializer import initialize_repo
from src.zip_utils import download_zip, extract_zip


@mark.parametrize(
    "url, expected",
    [
        ("https://github.com/user/repo", ("user", "repo")),
        ("http://github.com/user/repo.git", ("user", "repo")),
        ("https://github.com/user/repo/tree/main", ("user", "repo")),
        ("git@github.com:user/repo", ("user", "repo")),
        ("ssh://git@github.com/user/repo", ("user", "repo")),
    ],
)
def test_parse_github_url_valid(url: str, expected: tuple[str, str]) -> None:
    """Test that valid GitHub URLs are parsed correctly."""
    assert parse_github_url(url) == expected


@mark.parametrize(
    "url",
    [
        "https://gitlab.com/user/repo",
        "not a url",
        "user/repo",
    ],
)
def test_parse_github_url_invalid(url: str) -> None:
    """Test that invalid GitHub URLs raise ValueError."""
    with raises(ValueError):
        parse_github_url(url)

        # FIXME: FAILED test_gitripper.py::test_parse_github_url_invalid[https://gitlab.com/user/repo] - Failed: DID NOT RAISE <class 'ValueError'>
        #     FAILED test_gitripper.py::test_parse_github_url_invalid[not a url] - Failed: DID NOT RAISE <class 'ValueError'>
        #     FAILED test_gitripper.py::test_parse_github_url_invalid[user/repo] - Failed: DID NOT RAISE <class 'ValueError'>


@patch("src.default_branch_getter.get")
def test_get_default_branch_success(mock_get: MagicMock) -> None:
    """Test successfully getting the default branch."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"default_branch": "develop"}
    mock_get.return_value = mock_response
    branch = get_default_branch("owner", "repo", "token")
    assert branch == "develop"

    mock_get.assert_called_once_with(
        "https://api.github.com/repos/owner/repo",
        headers={"Authorization": "token token"},
        timeout=30,
    )


@patch("src.default_branch_getter.get")
def test_get_default_branch_not_found(mock_get: MagicMock) -> None:
    """Test handling of a 404 error when getting the default branch."""
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_get.return_value = mock_response

    with raises(FileNotFoundError):
        get_default_branch("owner", "repo", None)


@patch("src.default_branch_getter.get")
def test_get_default_branch_api_error(mock_get: MagicMock) -> None:
    """Test handling of a generic API error."""
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Server Error"
    mock_get.return_value = mock_response

    with raises(RuntimeError):
        get_default_branch("owner", "repo", None)


def test_get_default_branch_no_owner() -> None:
    """Test that ValueError is raised if owner is None."""
    with raises(ValueError):
        get_default_branch(None, "repo", "token")


@patch("src.gitripper.get")
def test_download_zip_success(mock_get: MagicMock, tmp_path: Path) -> None:
    """Test successful download of a zip archive."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.iter_content.return_value = [b"zip", b"content"]
    mock_get.return_value.__enter__.return_value = mock_response
    zip_path = download_zip("owner", "repo", "main", "token", tmp_path)
    expected_path = tmp_path / "repo-main.zip"
    assert zip_path == expected_path
    assert expected_path.read_bytes() == b"zipcontent"
    mock_get.assert_called_once()
    headers = mock_get.call_args.kwargs["headers"]
    assert headers["Authorization"] == "token token"


@patch("src.gitripper.get")
def test_download_zip_not_found(mock_get: MagicMock, tmp_path: Path) -> None:
    """Test handling of a 404 error during zip download."""
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_get.return_value.__enter__.return_value = mock_response

    with raises(FileNotFoundError):
        download_zip("owner", "repo", "main", None, tmp_path)


@patch("src.gitripper.get")
def test_download_zip_error(mock_get: MagicMock, tmp_path: Path) -> None:
    """Test handling of a generic error during zip download."""
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Server Error"
    mock_get.return_value.__enter__.return_value = mock_response

    with raises(RuntimeError):
        download_zip("owner", "repo", "main", None, tmp_path)


@patch("src.gitripper.get")
def test_download_zip_redirect(mock_get: MagicMock, tmp_path: Path) -> None:
    """Test handling of a redirect during zip download."""
    mock_response = MagicMock()
    mock_response.status_code = 302
    mock_get.return_value.__enter__.return_value = mock_response

    with raises(RuntimeError, match="Unexpected redirect: 302"):
        download_zip("owner", "repo", "main", None, tmp_path)


def test_extract_zip(tmp_path: Path) -> None:
    """Test extraction of a zip file."""
    zip_path = tmp_path / "test.zip"
    dest_dir = tmp_path / "dest"
    content_file = "test.txt"
    content = b"hello world"

    with ZipFile(zip_path, "w") as zf:
        zf.writestr(f"toplevel-dir/{content_file}", content)

    extract_zip(zip_path, dest_dir)

    assert (dest_dir / content_file).exists()
    assert (dest_dir / content_file).read_bytes() == content


def test_extract_empty_zip(tmp_path: Path) -> None:
    """Test that extracting an empty zip raises an error."""
    zip_path = tmp_path / "empty.zip"
    dest_dir = tmp_path / "dest"

    with ZipFile(zip_path, "w"):
        pass

    with raises(RuntimeError, match="Zip archive is empty."):
        extract_zip(zip_path, dest_dir)


def test_remove_embedded_git(tmp_path: Path) -> None:
    """Test removal of embedded .git directories."""
    git_dir = tmp_path / "sub" / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "config").touch()
    assert git_dir.exists()
    remove_embedded_git(tmp_path)
    assert not git_dir.exists()


@patch("src.gitripper.run")
def test_check_git_installed_success(mock_run: MagicMock) -> None:
    """Test that check_git_installed passes when git is present."""
    check_git_installed()
    mock_run.assert_called_once()
    assert mock_run.call_args[0][0][0] == "git"


@patch("src.gitripper.run", side_effect=FileNotFoundError)
def test_check_git_installed_failure(mock_run: MagicMock) -> None:
    """Test that check_git_installed raises EnvironmentError when git
    is missing.
    """
    with raises(EnvironmentError):
        check_git_installed()


@patch("src.gitripper.run")
def test_initialize_repo(mock_run: MagicMock, tmp_path: Path) -> None:
    """Test the git repository initialization process."""
    author_name = "Test User"
    author_email = "test@example.com"
    remote = "https://github.com/user/repo.git"
    initialize_repo(tmp_path, author_name, author_email, remote)

    expected_calls = [
        call(["git", "init"], cwd=str(tmp_path), check=True),
        call(
            ["git", "config", "user.name", author_name],
            cwd=str(tmp_path),
            check=True,
        ),
        call(
            ["git", "config", "user.email", author_email],
            cwd=str(tmp_path),
            check=True,
        ),
        call(["git", "add", "."], cwd=str(tmp_path), check=True),
        call(
            ["git", "commit", "-m", "Initial commit"],
            cwd=str(tmp_path),
            check=True,
        ),
        call(
            ["git", "remote", "add", "origin", remote],
            cwd=str(tmp_path),
            check=True,
        ),
    ]

    mock_run.assert_has_calls(expected_calls)


@patch("src.gitripper.main")
def test_main_entrypoint(mock_main: MagicMock) -> None:
    """Test the main entrypoint calls the main function."""
    with patch("gitripper.__name__", "__main__"):
        from src import gitripper

        mock_main.assert_not_called()
        reload(gitripper)

        try:
            gitripper.main()
            assert False, "should have raised an exception"

        except SystemExit:
            pass


@patch("src.gitripper.initialize_repo")
@patch("src.gitripper.remove_embedded_git")
@patch("src.gitripper.extract_zip")
@patch("src.gitripper.download_zip")
@patch("src.gitripper.get_default_branch")
@patch("src.gitripper.check_git_installed")
@patch("src.gitripper.parse_github_url")
@patch("argparse.ArgumentParser.parse_args")
@patch("builtins.print")
def test_main_success_flow(
    mock_print: MagicMock,
    mock_parse_args: MagicMock,
    mock_parse_url: MagicMock,
    mock_check_git: MagicMock,
    mock_get_branch: MagicMock,
    mock_download: MagicMock,
    mock_extract: MagicMock,
    mock_remove_git: MagicMock,
    mock_init_repo: MagicMock,
    tmp_path: Path,
) -> None:
    """Test the successful execution path of the main function."""
    args = MagicMock()
    args.url = "https://github.com/user/repo"
    args.branch = "develop"
    args.token = "fake_token"
    args.dest = str(tmp_path)
    args.author_name = "Test"
    args.author_email = "test@test.com"
    args.remote = "git@github.com:user/repo.git"
    args.force = False
    mock_parse_args.return_value = args
    mock_parse_url.return_value = ("user", "repo")
    zip_path = tmp_path / "repo-develop.zip"
    mock_download.return_value = zip_path
    main()
    mock_print.assert_called()
    mock_parse_url.assert_called_once_with(args.url)
    mock_check_git.assert_called_once()
    mock_get_branch.assert_not_called()  # Branch was specified
    mock_download.assert_called_once()
    mock_extract.assert_called_once_with(zip_path, tmp_path)
    mock_remove_git.assert_called_once_with(tmp_path)

    mock_init_repo.assert_called_once_with(
        tmp_path, args.author_name, args.author_email, args.remote
    )


@patch("argparse.ArgumentParser.parse_args")
@patch("src.gitripper.parse_github_url", side_effect=ValueError("Invalid URL"))
@patch("builtins.print")
def test_main_invalid_url(
    mock_print: MagicMock,
    mock_parse_url: MagicMock,
    mock_parse_args: MagicMock,
) -> None:
    """Test main function handling of an invalid GitHub URL."""
    mock_parse_args.return_value = MagicMock(url="invalid")

    with raises(SystemExit) as e:
        main()

    assert e.value.code == 2


@patch("pathlib.Path.exists", return_value=True)
@patch("pathlib.Path.iterdir", return_value=[Path("some_file")])
@patch("argparse.ArgumentParser.parse_args")
@patch("src.gitripper.parse_github_url")
@patch("builtins.print")
def test_main_dest_exists_not_empty_no_force(
    mock_print: MagicMock,
    mock_parse_url: MagicMock,
    mock_parse_args: MagicMock,
    mock_iterdir: MagicMock,
    mock_exists: MagicMock,
) -> None:
    """Test main function when destination exists and --force is not
    used.
    """
    args = MagicMock(url="https://github.com/u/r", force=False, dest="somedir")
    mock_parse_args.return_value = args
    mock_parse_url.return_value = ("u", "r")

    with raises(SystemExit) as e:
        main()

    assert e.value.code == 3


@patch("shutil.rmtree", side_effect=OSError("Permission denied"))
@patch("pathlib.Path.is_dir", return_value=True)
@patch("pathlib.Path.exists", return_value=True)
@patch("pathlib.Path.iterdir", return_value=[Path("some_file")])
@patch("argparse.ArgumentParser.parse_args")
@patch("src.gitripper.parse_github_url")
@patch("builtins.print")
def test_main_dest_exists_force_rm_fails(
    mock_print: MagicMock,
    mock_parse_url: MagicMock,
    mock_parse_args: MagicMock,
    mock_iterdir: MagicMock,
    mock_exists: MagicMock,
    mock_isdir: MagicMock,
    mock_rmtree: MagicMock,
) -> None:
    """Test main function when --force is used but removal fails."""
    args = MagicMock(url="https://github.com/u/r", force=True, dest="somedir")
    mock_print.assert_not_called()
    mock_parse_args.return_value = args
    mock_parse_url.return_value = ("u", "r")
    mock_isdir.return_value = True
    mock_exists.return_value = True
    mock_iterdir.return_value = [Path("some_file")]
    mock_rmtree.side_effect = OSError("Permission denied")

    with raises(SystemExit) as e:
        main()

    assert e.value.code == 4


@patch("pathlib.Path.unlink", side_effect=OSError("Permission denied"))
@patch("pathlib.Path.is_dir", return_value=False)
@patch("pathlib.Path.exists", return_value=True)
@patch("pathlib.Path.iterdir", return_value=[])
@patch("argparse.ArgumentParser.parse_args")
@patch("src.gitripper.parse_github_url")
@patch("builtins.print")
def test_main_dest_exists_force_rm_file_fails(
    mock_print: MagicMock,
    mock_parse_url: MagicMock,
    mock_parse_args: MagicMock,
    mock_iterdir: MagicMock,
    mock_exists: MagicMock,
    mock_isdir: MagicMock,
    mock_unlink: MagicMock,
) -> None:
    """Test main function when --force is used but removing a file
    fails."""
    args = MagicMock(url="https://github.com/u/r", force=True, dest="somefile")
    mock_parse_args.return_value = args
    mock_parse_url.return_value = ("u", "r")
    mock_isdir.return_value = False
    mock_exists.return_value = True
    mock_iterdir.return_value = []
    mock_unlink.side_effect = OSError("Permission denied")
    mock_print.assert_not_called()

    with raises(SystemExit) as e:
        main()

    assert e.value.code == 4


@patch(
    "gitripper.check_git_installed",
    side_effect=EnvironmentError("git not found"),
)
@patch("argparse.ArgumentParser.parse_args")
@patch("src.gitripper.parse_github_url")
@patch("builtins.print")
def test_main_git_not_installed(
    mock_print: MagicMock,
    mock_parse_url: MagicMock,
    mock_parse_args: MagicMock,
    mock_check_git: MagicMock,
) -> None:
    """Test main function when git is not installed."""
    mock_parse_args.return_value = MagicMock(
        url="https://github.com/u/r", dest="d", force=False
    )

    mock_parse_url.return_value = ("u", "r")

    with raises(SystemExit) as e:
        main()

    assert e.value.code == 5


@patch("src.gitripper.download_zip", side_effect=Exception("Download failed"))
@patch("src.gitripper.get_default_branch")
@patch("src.gitripper.check_git_installed")
@patch("src.gitripper.parse_github_url")
@patch("argparse.ArgumentParser.parse_args")
@patch("builtins.print")
def test_main_download_fails(
    mock_print: MagicMock,
    mock_parse_args: MagicMock,
    mock_parse_url: MagicMock,
    mock_check_git: MagicMock,
    mock_get_branch: MagicMock,
    mock_download: MagicMock,
) -> None:
    """Test main function when the download fails."""
    args = MagicMock(
        url="https://github.com/u/r",
        dest="d",
        branch=None,
        token=None,
        force=False,
    )

    mock_parse_args.return_value = args
    mock_parse_url.return_value = ("u", "r")
    mock_get_branch.return_value = "main"
    mock_print.assert_not_called()
    mock_check_git.assert_not_called()
    mock_parse_url.assert_not_called()
    mock_download.side_effect = Exception("Download failed")

    with raises(SystemExit) as e:
        main()

    assert e.value.code == 6


@patch("src.gitripper.extract_zip", side_effect=Exception("Extract failed"))
@patch("src.gitripper.download_zip")
@patch("src.gitripper.get_default_branch")
@patch("src.gitripper.check_git_installed")
@patch("src.gitripper.parse_github_url")
@patch("argparse.ArgumentParser.parse_args")
@patch("builtins.print")
def test_main_extract_fails(
    mock_print: MagicMock,
    mock_parse_args: MagicMock,
    mock_parse_url: MagicMock,
    mock_check_git: MagicMock,
    mock_get_branch: MagicMock,
    mock_download: MagicMock,
    mock_extract: MagicMock,
) -> None:
    """Test main function when extraction fails."""
    args = MagicMock(
        url="https://github.com/u/r",
        dest="d",
        branch=None,
        token=None,
        force=False,
    )

    mock_parse_args.return_value = args
    mock_parse_url.return_value = ("u", "r")
    mock_get_branch.return_value = "main"
    mock_download.return_value = Path("some.zip")
    mock_print.assert_not_called()
    mock_check_git.assert_not_called()
    mock_parse_url.assert_not_called()
    mock_extract.side_effect = Exception("Extract failed")

    with raises(SystemExit) as e:
        main()

    assert e.value.code == 7


@patch("src.gitripper.initialize_repo", side_effect=Exception("Init failed"))
@patch("src.gitripper.remove_embedded_git")
@patch("src.gitripper.extract_zip")
@patch("src.gitripper.download_zip")
@patch("src.gitripper.get_default_branch")
@patch("src.gitripper.check_git_installed")
@patch("src.gitripper.parse_github_url")
@patch("argparse.ArgumentParser.parse_args")
@patch("builtins.print")
def test_main_init_fails(
    mock_print: MagicMock,
    mock_parse_args: MagicMock,
    mock_parse_url: MagicMock,
    mock_check_git: MagicMock,
    mock_get_branch: MagicMock,
    mock_download: MagicMock,
    mock_extract: MagicMock,
    mock_remove_git: MagicMock,
    mock_init_repo: MagicMock,
) -> None:
    """Test main function when git initialization fails."""
    args = MagicMock(
        url="https://github.com/u/r",
        dest="d",
        branch=None,
        token=None,
        force=False,
    )

    mock_parse_args.return_value = args
    mock_parse_url.return_value = ("u", "r")
    mock_get_branch.return_value = "main"
    mock_download.return_value = Path("some.zip")
    mock_print.assert_not_called()
    mock_check_git.assert_not_called()
    mock_parse_url.assert_not_called()
    mock_init_repo.side_effect = Exception("Init failed")
    mock_extract.return_value = None
    mock_remove_git.return_value = None

    with raises(SystemExit) as e:
        main()

    assert e.value.code == 8


@patch("src.gitripper.initialize_repo")
@patch("src.gitripper.extract_zip")
@patch("src.gitripper.download_zip")
@patch("src.gitripper.get_default_branch", side_effect=Exception("API error"))
@patch("src.gitripper.check_git_installed")
@patch("src.gitripper.parse_github_url")
@patch("argparse.ArgumentParser.parse_args")
@patch("builtins.print")
def test_main_get_default_branch_fails(
    mock_print: MagicMock,
    mock_parse_args: MagicMock,
    mock_parse_url: MagicMock,
    mock_check_git: MagicMock,
    mock_get_branch: MagicMock,
    mock_download: MagicMock,
    mock_extract: MagicMock,
    mock_init_repo: MagicMock,
) -> None:
    """Test main function when getting default branch fails; should fall
    back to 'main'.
    """
    args = MagicMock(
        url="https://github.com/u/r",
        dest="d",
        branch=None,
        token=None,
        force=False,
        author_name=None,
        author_email=None,
        remote=None,
    )

    mock_parse_args.return_value = args
    mock_parse_url.return_value = ("u", "r")

    with patch("sys.exit") as mock_exit:
        main()
        mock_exit.assert_not_called()

    mock_download.assert_called_once()
    assert mock_download.call_args.args[2] == "main"
    mock_extract.assert_called_once()
    mock_init_repo.assert_called_once()
