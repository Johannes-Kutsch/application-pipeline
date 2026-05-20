from pathlib import Path

import pytest


@pytest.fixture
def test_job_section() -> str:
    text = (
        Path(__file__).parent.parent / ".github" / "workflows" / "publish.yml"
    ).read_text()
    return text[text.index("  test:") : text.index("  build:")]


def test_mypy_step_present_in_test_job(test_job_section: str) -> None:
    assert "mypy src" in test_job_section


def test_mypy_runs_after_ruff_and_before_pytest(test_job_section: str) -> None:
    ruff_idx = test_job_section.index("ruff check")
    mypy_idx = test_job_section.index("mypy src")
    pytest_idx = test_job_section.index("pytest")
    assert ruff_idx < mypy_idx < pytest_idx
