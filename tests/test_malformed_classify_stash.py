from __future__ import annotations

from pathlib import Path

from application_pipeline.llm.types import ExtractorMalformedError
from application_pipeline.malformed_classify_stash import (
    ListingDiagnosticFacts,
    stash_malformed_classify_artifact,
    stash_malformed_classify_exception,
    stash_malformed_classify_verdict,
)
from application_pipeline.parsers.types import PositionStub


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


def test_malformed_classify_stash_includes_raw_model_output_as_diagnostic_content(
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
        "**Error:** classifier output could not be parsed\n\n"
        "## Raw Model Output\n\n"
        "```text\n"
        "<verdict>{bad json}</verdict>\n"
        "```"
    )
    assert not _raw_output_path(tmp_path).exists()


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


def test_malformed_classify_stash_omits_whitespace_only_raw_model_output_section(
    tmp_path: Path,
) -> None:
    stash_malformed_classify_artifact(
        filesystem_root=tmp_path / "failures",
        listing=ListingDiagnosticFacts(
            source="test_src", url="https://example.com/job/42"
        ),
        error_classification="ExtractorMalformedJSONError",
        error_message="classifier output could not be parsed",
        raw_model_output=" \n\t ",
    )

    assert _markdown_path(tmp_path).read_text(encoding="utf-8") == (
        "**Source:** test_src\n"
        "**URL:** https://example.com/job/42\n"
        "**Error Classification:** ExtractorMalformedJSONError\n"
        "**Error:** classifier output could not be parsed"
    )
    assert not _raw_output_path(tmp_path).exists()


def test_malformed_classify_stash_omits_empty_raw_model_output_section(
    tmp_path: Path,
) -> None:
    stash_malformed_classify_artifact(
        filesystem_root=tmp_path / "failures",
        listing=ListingDiagnosticFacts(
            source="test_src", url="https://example.com/job/42"
        ),
        error_classification="ExtractorMalformedJSONError",
        error_message="classifier output could not be parsed",
        raw_model_output="",
    )

    assert _markdown_path(tmp_path).read_text(encoding="utf-8") == (
        "**Source:** test_src\n"
        "**URL:** https://example.com/job/42\n"
        "**Error Classification:** ExtractorMalformedJSONError\n"
        "**Error:** classifier output could not be parsed"
    )
    assert not _raw_output_path(tmp_path).exists()


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


def test_malformed_classify_exception_uses_listing_and_error_details(
    tmp_path: Path,
) -> None:
    runtime_log = tmp_path / "logs" / "llm" / "agent-runtime" / "classify" / "call.log"
    stub = PositionStub(
        url="https://example.com/job/42",
        title="Senior Platform Engineer",
        source="test_src",
    )

    stash_malformed_classify_exception(
        filesystem_root=tmp_path / "failures",
        stub=stub,
        error=ExtractorMalformedError(
            "header must be a non-empty string",
            raw_response="<verdict>{bad json}</verdict>",
        ),
        agent_runtime_log_pointer=runtime_log,
    )

    assert _markdown_path(tmp_path).read_text(encoding="utf-8") == (
        "**Source:** test_src\n"
        "**URL:** https://example.com/job/42\n"
        "**Title:** Senior Platform Engineer\n"
        "**Error Classification:** ExtractorMalformedError\n"
        "**Error:** header must be a non-empty string\n\n"
        "## Agent Runtime Log\n\n"
        f"{runtime_log}\n\n"
        "## Raw Model Output\n\n"
        "```text\n"
        "<verdict>{bad json}</verdict>\n"
        "```"
    )


def test_malformed_classify_exception_redacts_prompt_and_body_without_reformatting_raw_output(
    tmp_path: Path,
) -> None:
    stub = PositionStub(
        url="https://example.com/job/42",
        title="Senior Platform Engineer",
        source="test_src",
    )
    prompt_text = "PROMPT BLOCK 1053"
    raw_description = "RAW DESCRIPTION BODY 1053"

    stash_malformed_classify_exception(
        filesystem_root=tmp_path / "failures",
        stub=stub,
        error=ExtractorMalformedError(
            "header must be a non-empty string",
            prompt=prompt_text,
            raw_response=(
                "<verdict>{bad json}</verdict>\n\n"
                "provider note: trailing comma near summary field\n\n"
                f"{prompt_text}\n"
                f"{raw_description}\n"
            ),
        ),
        raw_description=raw_description,
    )

    assert _markdown_path(tmp_path).read_text(encoding="utf-8") == (
        "**Source:** test_src\n"
        "**URL:** https://example.com/job/42\n"
        "**Title:** Senior Platform Engineer\n"
        "**Error Classification:** ExtractorMalformedError\n"
        "**Error:** header must be a non-empty string\n\n"
        "## Raw Model Output\n\n"
        "```text\n"
        "<verdict>{bad json}</verdict>\n\n"
        "provider note: trailing comma near summary field\n"
        "```"
    )


def test_malformed_classify_verdict_uses_listing_identity_and_runtime_pointer(
    tmp_path: Path,
) -> None:
    runtime_log = tmp_path / "logs" / "llm" / "agent-runtime" / "classify" / "call.log"
    stub = PositionStub(
        url="https://example.com/job/42",
        title="Senior Platform Engineer",
        source="test_src",
    )

    stash_malformed_classify_verdict(
        filesystem_root=tmp_path / "failures",
        stub=stub,
        agent_runtime_log_pointer=runtime_log,
    )

    assert _markdown_path(tmp_path).read_text(encoding="utf-8") == (
        "**Source:** test_src\n"
        "**URL:** https://example.com/job/42\n"
        "**Title:** Senior Platform Engineer\n"
        "**Error Classification:** malformed_classifier_verdict\n"
        "**Error:** malformed classifier verdict\n\n"
        "## Agent Runtime Log\n\n"
        f"{runtime_log}"
    )
