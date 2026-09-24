import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.sync_cookies import build_install_script, upload_and_install


@pytest.mark.parametrize(
    ("root", "target"),
    [
        ("/", "cookies.txt"),
        ("relative/project", "cookies.txt"),
        ("/srv/project", "."),
        ("/srv/project", "/srv/project"),
        ("/srv/project", "cookies/"),
        ("/srv/project", "cookies/."),
        ("/srv/project", "../other/cookies.txt"),
        ("/srv/project", "cookies/../cookies.txt"),
        ("/srv/project", "/srv/project-other/cookies.txt"),
        ("/srv/project", "/etc/passwd"),
        ("/srv/project", "cookies\n.txt"),
        ("/srv/project", "C:\\cookies.txt"),
        ("/srv/../project", "cookies.txt"),
    ],
)
def test_unsafe_target_is_rejected_before_transport(root, target):
    class NoTransport:
        def run(self, *args, **kwargs):
            pytest.fail("unsafe target must not reach SSH")

    with pytest.raises(SystemExit):
        upload_and_install(NoTransport(), b"cookies", root, target)


@pytest.fixture
def shell():
    if os.name == "nt":
        # Use Git's POSIX shell; Windows' bash.exe may invoke an unavailable WSL distro.
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        if candidate.is_file():
            return str(candidate)
        pytest.skip("Git Bash is required for local cookie installer tests on Windows")
    candidate = shutil.which("sh")
    if not candidate:
        pytest.skip("POSIX shell unavailable")
    return candidate


def _remote_path(path):
    value = path.resolve().as_posix()
    return f"/{value[0].lower()}{value[2:]}" if os.name == "nt" else value


def _install(shell, root, target="cookies.txt", payload=b"new cookies\n", prefix=""):
    setup = 'export PATH="/usr/bin:/bin:$PATH"\n' if os.name == "nt" else ""
    script = setup + prefix + build_install_script(_remote_path(root), target)
    return subprocess.run([shell, "-c", script], input=payload, capture_output=True, timeout=15)


def test_cookie_replacement_preserves_inode_and_cleans_temporaries(shell, tmp_path):
    cookie = tmp_path / "cookies.txt"
    cookie.write_bytes(b"old cookies\n")
    inode = cookie.stat().st_ino

    result = _install(shell, tmp_path)

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.strip() == b"CHANGED"
    assert cookie.read_bytes() == b"new cookies\n"
    assert cookie.stat().st_ino == inode
    assert sorted(path.name for path in tmp_path.iterdir()) == ["cookies.txt"]
    if os.name != "nt":
        assert cookie.stat().st_mode & 0o777 == 0o600

    unchanged = _install(shell, tmp_path)
    assert unchanged.returncode == 0, unchanged.stderr.decode()
    assert unchanged.stdout.strip() == b"UNCHANGED"
    assert cookie.stat().st_ino == inode


def test_nested_absolute_target_with_spaces(shell, tmp_path):
    target = _remote_path(tmp_path) + "/cookie storage/session.txt"

    result = _install(shell, tmp_path, target)

    assert result.returncode == 0, result.stderr.decode()
    assert (tmp_path / "cookie storage/session.txt").read_bytes() == b"new cookies\n"


def test_empty_docker_mount_directory_is_replaced(shell, tmp_path):
    cookie = tmp_path / "cookies.txt"
    cookie.mkdir()

    result = _install(shell, tmp_path)

    assert result.returncode == 0, result.stderr.decode()
    assert cookie.read_bytes() == b"new cookies\n"


def test_nonempty_directory_is_preserved(shell, tmp_path):
    cookie = tmp_path / "cookies.txt"
    cookie.mkdir()
    important = cookie / "important.txt"
    important.write_bytes(b"keep me")

    result = _install(shell, tmp_path)

    assert result.returncode != 0
    assert important.read_bytes() == b"keep me"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["cookies.txt"]


@pytest.mark.parametrize("link_parent", [False, True])
def test_symlinks_cannot_redirect_cookie_writes(shell, tmp_path, link_parent):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    original = outside / "cookies.txt"
    original.write_bytes(b"keep me")
    link = project / ("linked" if link_parent else "cookies.txt")
    try:
        link.symlink_to(outside if link_parent else original, target_is_directory=link_parent)
    except OSError:
        pytest.skip("creating symlinks requires additional privileges on this system")

    result = _install(shell, project, "linked/cookies.txt" if link_parent else "cookies.txt")

    assert result.returncode != 0
    assert original.read_bytes() == b"keep me"
    assert link.is_symlink()


def test_additional_hard_link_is_preserved(shell, tmp_path):
    original = tmp_path / "original.txt"
    original.write_bytes(b"keep me")
    (tmp_path / "cookies.txt").hardlink_to(original)

    result = _install(shell, tmp_path)

    assert result.returncode != 0
    assert original.read_bytes() == b"keep me"


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX FIFO")
def test_fifo_is_rejected_without_opening_it(shell, tmp_path):
    cookie = tmp_path / "cookies.txt"
    os.mkfifo(cookie)

    result = _install(shell, tmp_path)

    assert result.returncode != 0
    assert cookie.exists()


def test_empty_upload_preserves_existing_cookie(shell, tmp_path):
    cookie = tmp_path / "cookies.txt"
    cookie.write_bytes(b"keep me")

    result = _install(shell, tmp_path, payload=b"")

    assert result.returncode != 0
    assert cookie.read_bytes() == b"keep me"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["cookies.txt"]


def test_failed_write_restores_original_cookie_in_same_inode(shell, tmp_path):
    cookie = tmp_path / "cookies.txt"
    cookie.write_bytes(b"keep me")
    inode = cookie.stat().st_ino
    fail_install = """cat() {
  case "${1-}" in
    */.cookie-sync.*) printf 'partial upload'; return 1 ;;
  esac
  command cat "$@"
}
"""

    result = _install(shell, tmp_path, prefix=fail_install)

    assert result.returncode != 0
    assert cookie.read_bytes() == b"keep me"
    assert cookie.stat().st_ino == inode
    assert sorted(path.name for path in tmp_path.iterdir()) == ["cookies.txt"]
