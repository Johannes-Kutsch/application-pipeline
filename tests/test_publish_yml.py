from pathlib import Path


def _publish_yml_text() -> str:
    path = Path(__file__).parent.parent / ".github" / "workflows" / "publish.yml"
    return path.read_text()


def test_mypy_step_present_in_test_job():
    text = _publish_yml_text()
    # Confirm we're looking inside the test job section
    test_job_start = text.index("  test:")
    build_job_start = text.index("  build:")
    test_job_section = text[test_job_start:build_job_start]
    assert "mypy src" in test_job_section, "mypy src not found in test job"


def test_mypy_runs_after_ruff_and_before_pytest():
    text = _publish_yml_text()
    test_job_start = text.index("  test:")
    build_job_start = text.index("  build:")
    test_job_section = text[test_job_start:build_job_start]

    ruff_idx = test_job_section.index("ruff check")
    mypy_idx = test_job_section.index("mypy src")
    pytest_idx = test_job_section.index("pytest")
    assert ruff_idx < mypy_idx < pytest_idx, (
        f"Expected ruff({ruff_idx}) < mypy({mypy_idx}) < pytest({pytest_idx})"
    )
