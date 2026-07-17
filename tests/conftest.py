import os
import shutil as _shutil
import sys
from pathlib import Path
from typing import Generator

import pytest

# Make test-tree helpers (e.g. fake_status_display) importable by name.
sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(autouse=True)
def _stub_opencode_on_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Generator[None, None, None]:
    # Stub shutil.which for in-process calls (most tests).
    _real_which = _shutil.which
    monkeypatch.setattr(
        "shutil.which",
        lambda name, *args, **kwargs: "/usr/local/bin/opencode"
        if name == "opencode"
        else _real_which(name, *args, **kwargs),
    )

    # Create a real executable so subprocess-based tests also find opencode.
    fake_bin = tmp_path / "_fake_bin"
    fake_bin.mkdir(exist_ok=True)
    fake_opencode = fake_bin / "opencode"
    fake_opencode.write_text("#!/bin/sh\nexit 0\n")
    fake_opencode.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")

    yield


def pytest_sessionfinish(session, exitstatus):
    if exitstatus == 5:
        session.exitstatus = 0
