import sys
from pathlib import Path

# Make test-tree helpers (e.g. fake_status_display) importable by name.
sys.path.insert(0, str(Path(__file__).parent))


def pytest_sessionfinish(session, exitstatus):
    if exitstatus == 5:
        session.exitstatus = 0
