from __future__ import annotations

from pathlib import Path

from application_pipeline.malformed_classify_stash import (
    ListingDiagnosticFacts,
    stash_malformed_classify_artifact,
)


def _markdown_path(tmp_path: Path) -> Path:
    return tmp_path / "failures" / "malformed" / "test_src-example.com-job-42.md"


def _raw_output_path(tmp_path: Path) -> Path:
    return tmp_path / "failures" / "malformed" / "test_src-example.com-job-42.txt"


def test_malformed_classify_stash_writes_listing_facts_error_and_runtime_log_pointer(
    tmp_path: Path,
) -> None:
    runtime_log = tmp_path / "logs" / "llm" / "agent-runtime" / "classify" / "call.log"

    stash_malformed_classify_artifact(
        filesystem_root=tmp_path / "failures",
        listing=ListingDiagnosticFacts(
            source="test_src", url="https://example.com/job/42"
        ),
        error_classification="ExtractorMalformedError",
        error_message="header must be a non-empty string",
        agent_runtime_log_pointer=runtime_log,
    )

    assert _markdown_path(tmp_path).read_text(encoding="utf-8") == (
        "**Source:** test_src\n"
        "**URL:** https://example.com/job/42\n"
        "**Error Classification:** ExtractorMalformedError\n"
        "**Error:** header must be a non-empty string\n\n"
        "## Agent Runtime Log\n\n"
        f"{runtime_log}"
    )
    assert not _raw_output_path(tmp_path).exists()


def test_malformed_classify_stash_writes_opaque_agent_runtime_log_pointer_text(
    tmp_path: Path,
) -> None:
    runtime_pointer = "agent-runtime://classify/run-42?event=3#result"

    stash_malformed_classify_artifact(
        filesystem_root=tmp_path / "failures",
        listing=ListingDiagnosticFacts(
            source="test_src", url="https://example.com/job/42"
        ),
        error_classification="ExtractorMalformedError",
        error_message="header must be a non-empty string",
        agent_runtime_log_pointer=runtime_pointer,
    )

    assert _markdown_path(tmp_path).read_text(encoding="utf-8") == (
        "**Source:** test_src\n"
        "**URL:** https://example.com/job/42\n"
        "**Error Classification:** ExtractorMalformedError\n"
        "**Error:** header must be a non-empty string\n\n"
        "## Agent Runtime Log\n\n"
        f"{runtime_pointer}"
    )
    assert not _raw_output_path(tmp_path).exists()


def test_malformed_classify_stash_writes_raw_model_output_only_when_present(
    tmp_path: Path,
) -> None:
    raw_model_output = "<verdict>{bad json}</verdict>"

    stash_malformed_classify_artifact(
        filesystem_root=tmp_path / "failures",
        listing=ListingDiagnosticFacts(
            source="test_src", url="https://example.com/job/42"
        ),
        error_classification="ExtractorMalformedJSONError",
        error_message="classifier output could not be parsed",
        raw_model_output=raw_model_output,
    )

    assert _markdown_path(tmp_path).read_text(encoding="utf-8") == (
        "**Source:** test_src\n"
        "**URL:** https://example.com/job/42\n"
        "**Error Classification:** ExtractorMalformedJSONError\n"
        "**Error:** classifier output could not be parsed"
    )
    assert _raw_output_path(tmp_path).read_text(encoding="utf-8") == raw_model_output


def test_malformed_classify_stash_reuses_listing_slug_for_http_urls(
    tmp_path: Path,
) -> None:
    stash_malformed_classify_artifact(
        filesystem_root=tmp_path / "failures",
        listing=ListingDiagnosticFacts(
            source="test_src", url="http://example.com/job/42"
        ),
        error_classification="malformed_classifier_verdict",
        error_message="malformed classifier verdict",
    )

    assert (
        tmp_path / "failures" / "malformed" / "test_src-example.com-job-42.md"
    ).exists()


def test_malformed_classify_stash_returns_written_markdown_path(tmp_path: Path) -> None:
    artifact_path = stash_malformed_classify_artifact(
        filesystem_root=tmp_path / "failures",
        listing=ListingDiagnosticFacts(
            source="test_src", url="https://example.com/job/42"
        ),
        error_classification="ExtractorMalformedError",
        error_message="header must be a non-empty string",
    )

    assert artifact_path == _markdown_path(tmp_path)


def test_malformed_classify_stash_identifies_title_and_error_classification(
    tmp_path: Path,
) -> None:
    stash_malformed_classify_artifact(
        filesystem_root=tmp_path / "failures",
        listing=ListingDiagnosticFacts(
            source="test_src",
            url="https://example.com/job/42",
            title="Senior Platform Engineer",
        ),
        error_classification="ExtractorMalformedJSONError",
        error_message="classifier output could not be parsed",
    )

    assert _markdown_path(tmp_path).read_text(encoding="utf-8") == (
        "**Source:** test_src\n"
        "**URL:** https://example.com/job/42\n"
        "**Title:** Senior Platform Engineer\n"
        "**Error Classification:** ExtractorMalformedJSONError\n"
        "**Error:** classifier output could not be parsed"
    )
