import sys
from pathlib import Path

import pytest

from application_pipeline.atomic_write import write_atomic


def test_write_atomic_creates_file_at_target(tmp_path: Path) -> None:
    target = tmp_path / "output.json"
    payload = b'{"key": "value"}'

    write_atomic(target, payload)

    assert target.exists()
    assert target.read_bytes() == payload


def test_write_atomic_overwrites_existing_target(tmp_path: Path) -> None:
    target = tmp_path / "output.json"
    target.write_bytes(b"old content")
    new_payload = b"new content"

    write_atomic(target, new_payload)

    assert target.read_bytes() == new_payload


def test_write_atomic_leaves_no_tmp_artefact(tmp_path: Path) -> None:
    target = tmp_path / "output.json"

    write_atomic(target, b"data")

    assert not (tmp_path / "output.json.tmp").exists()


def test_write_atomic_raises_oserror_when_parent_missing(tmp_path: Path) -> None:
    target = tmp_path / "nonexistent" / "output.json"

    with pytest.raises(OSError):
        write_atomic(target, b"data")


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="chmod(0o555) does not block owner writes on a directory on Windows; "
    "the perm bits only toggle the read-only file attribute, not directory ACLs.",
)
def test_write_atomic_raises_oserror_when_parent_readonly(tmp_path: Path) -> None:
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    readonly_dir.chmod(0o555)
    target = readonly_dir / "output.json"

    try:
        with pytest.raises(OSError):
            write_atomic(target, b"data")
    finally:
        readonly_dir.chmod(0o755)
