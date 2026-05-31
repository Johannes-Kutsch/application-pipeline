from __future__ import annotations

from application_pipeline.classify_stage import (
    ClassifyReadySubmission,
    ClassifyRequest,
)
from application_pipeline.parsers import PositionStub


def test_classify_stage_builds_classify_request_from_classify_ready_submission() -> (
    None
):
    stub = PositionStub(
        url="https://example.com/role",
        title="Platform Engineer",
        source="test",
    )
    submission = ClassifyReadySubmission(
        listing_id=7,
        stub=stub,
        raw_description="Raw description for classify handoff",
    )

    request = ClassifyRequest(submission=submission, parser_id="parser.test")

    assert request.submission.listing_id == 7
    assert request.submission.stub == stub
    assert request.submission.raw_description == "Raw description for classify handoff"
    assert request.parser_id == "parser.test"
