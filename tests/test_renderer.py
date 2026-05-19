from dataclasses import replace

import pytest

from application_pipeline import Layout, MatchVerdict
from application_pipeline.parsers.types import Position, PositionStub
from application_pipeline.renderer import render


@pytest.fixture
def layout() -> Layout:
    return Layout(
        placeholder_groups={},
        card_template="",
    )


@pytest.fixture
def stub() -> PositionStub:
    return PositionStub(
        url="https://example.com/job/1",
        title="Senior Engineer",
        source="test-source",
        company="Acme GmbH",
        location="Berlin",
    )


@pytest.fixture
def position(stub: PositionStub) -> Position:
    return Position(
        stub=stub,
        raw_description="Some description here.",
    )


@pytest.fixture
def green_verdict() -> MatchVerdict:
    return MatchVerdict(
        matched=["Python", "Data Engineering"],
        missing=["Rust"],
        summary="Strong fit overall.",
    )


@pytest.fixture
def amber_verdict() -> MatchVerdict:
    return MatchVerdict(
        matched=["Python"],
        missing=["Rust", "Go"],
        summary="Partial fit.",
    )


@pytest.fixture
def red_verdict() -> MatchVerdict:
    return MatchVerdict(
        matched=[],
        missing=["Rust", "C++"],
        summary="Poor fit.",
    )


# --- Layout has no headline_template attribute ---


def test_layout_has_no_headline_template() -> None:
    assert not hasattr(
        Layout(
            placeholder_groups={},
            card_template="",
        ),
        "headline_template",
    )


# --- H1: location-led, no number, no emoji/span ---


def test_card_h1_is_location_led(
    position: Position, green_verdict: MatchVerdict
) -> None:
    result = render(
        position,
        green_verdict,
        Layout(
            placeholder_groups={},
            card_template="",
        ),
    )
    first_line = result.splitlines()[0]
    assert first_line == "# Acme GmbH · Senior Engineer · Berlin"
    assert "<span" not in first_line
    assert "🟢" not in first_line


def test_card_h1_contains_no_number_prefix(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    result = render(position, green_verdict, layout)
    first_line = result.splitlines()[0]
    import re

    assert not re.match(r".*\d+\.", first_line)


# --- location_segment rules ---


def test_location_segment_location_only_when_work_model_none(
    layout: Layout, green_verdict: MatchVerdict
) -> None:
    pos = Position(
        stub=PositionStub(
            url="https://example.com/job/1", title="Dev", source="s", location="Munich"
        ),
        raw_description="",
        work_model=None,
    )
    result = render(pos, green_verdict, layout)
    assert result.startswith("# Dev · Munich")


def test_location_segment_location_only_when_work_model_on_site(
    layout: Layout, green_verdict: MatchVerdict
) -> None:
    pos = Position(
        stub=PositionStub(
            url="https://example.com/job/1", title="Dev", source="s", location="Munich"
        ),
        raw_description="",
        work_model="on-site",
    )
    result = render(pos, green_verdict, layout)
    assert result.startswith("# Dev · Munich")
    assert "Hybrid" not in result.splitlines()[0]
    assert "Remote" not in result.splitlines()[0]


def test_location_segment_hybrid_appended(
    layout: Layout, green_verdict: MatchVerdict
) -> None:
    pos = Position(
        stub=PositionStub(
            url="https://example.com/job/1", title="Dev", source="s", location="Munich"
        ),
        raw_description="",
        work_model="hybrid",
    )
    result = render(pos, green_verdict, layout)
    assert result.startswith("# Dev · Munich (Hybrid)")


def test_location_segment_remote_appended(
    layout: Layout, green_verdict: MatchVerdict
) -> None:
    pos = Position(
        stub=PositionStub(
            url="https://example.com/job/1", title="Dev", source="s", location="Munich"
        ),
        raw_description="",
        work_model="remote",
    )
    result = render(pos, green_verdict, layout)
    assert result.startswith("# Dev · Munich (Remote)")


def test_location_segment_unknown_when_location_none_work_model_none(
    layout: Layout, green_verdict: MatchVerdict
) -> None:
    pos = Position(
        stub=PositionStub(
            url="https://example.com/job/1", title="Dev", source="s", location=None
        ),
        raw_description="",
        work_model=None,
    )
    result = render(pos, green_verdict, layout)
    assert result.startswith("# Dev · Unknown Location")
    assert "(Hybrid)" not in result.splitlines()[0]
    assert "(Remote)" not in result.splitlines()[0]


def test_location_segment_unknown_hybrid_when_location_none(
    layout: Layout, green_verdict: MatchVerdict
) -> None:
    pos = Position(
        stub=PositionStub(
            url="https://example.com/job/1", title="Dev", source="s", location=None
        ),
        raw_description="",
        work_model="hybrid",
    )
    result = render(pos, green_verdict, layout)
    assert result.startswith("# Dev · Unknown Location (Hybrid)")


# --- meta line: hide-if-empty ---


def test_meta_line_absent_when_all_three_none(
    layout: Layout, stub: PositionStub, green_verdict: MatchVerdict
) -> None:
    pos = Position(
        stub=stub,
        raw_description="",
        posted_date=None,
        contract_type=None,
        employment_type=None,
    )
    result = render(pos, green_verdict, layout)
    lines = result.splitlines()
    assert lines[0].startswith("# ")
    assert lines[1] == ""
    assert lines[2] == "## AI Assessment"


def test_meta_line_present_when_posted_date_set(
    layout: Layout, stub: PositionStub, green_verdict: MatchVerdict
) -> None:
    from datetime import date

    pos = Position(stub=stub, raw_description="", posted_date=date(2026, 1, 15))
    result = render(pos, green_verdict, layout)
    assert "2026-01-15" in result


def test_meta_line_joins_set_fields_only(
    layout: Layout, stub: PositionStub, green_verdict: MatchVerdict
) -> None:
    pos = Position(
        stub=stub,
        raw_description="",
        contract_type="permanent",
        employment_type=None,
    )
    result = render(pos, green_verdict, layout)
    assert "permanent" in result
    assert "None" not in result


def test_meta_line_all_three_present(
    layout: Layout, stub: PositionStub, green_verdict: MatchVerdict
) -> None:
    from datetime import date

    pos = Position(
        stub=stub,
        raw_description="",
        posted_date=date(2026, 3, 1),
        contract_type="freelance",
        employment_type="part-time",
    )
    result = render(pos, green_verdict, layout)
    assert "2026-03-01 · freelance · part-time" in result


# --- salary: hide-if-empty ---


def test_salary_line_absent_when_none(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    pos = replace(position, salary=None)
    result = render(pos, green_verdict, layout)
    assert "**Salary:**" not in result


def test_salary_line_present_when_set(
    layout: Layout, stub: PositionStub, green_verdict: MatchVerdict
) -> None:
    pos = Position(stub=stub, raw_description="", salary="€80 000")
    result = render(pos, green_verdict, layout)
    assert "**Salary:** €80 000" in result


# --- AI Assessment section ---


def test_ai_assessment_heading_present(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    result = render(position, green_verdict, layout)
    assert "## AI Assessment" in result


def test_summary_follows_ai_assessment_heading(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    result = render(position, green_verdict, layout)
    lines = result.splitlines()
    ai_idx = lines.index("## AI Assessment")
    assert lines[ai_idx + 1] == ""
    assert lines[ai_idx + 2] == "Strong fit overall."


# --- matched / missing: bullet lists, hide-if-empty ---


def test_matched_rendered_as_bullet_list(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    result = render(position, green_verdict, layout)
    assert "- Python\n- Data Engineering" in result


def test_missing_rendered_as_bullet_list(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    result = render(position, green_verdict, layout)
    assert "- Rust" in result


def test_matched_label_and_bullets_absent_when_list_empty(
    layout: Layout, position: Position, red_verdict: MatchVerdict
) -> None:
    result = render(position, red_verdict, layout)
    assert "**Matched:**" not in result


def test_missing_label_and_bullets_absent_when_list_empty(
    layout: Layout, green_verdict: MatchVerdict
) -> None:
    verdict = replace(green_verdict, missing=[])
    pos = Position(
        stub=PositionStub(url="https://example.com/job/1", title="Dev", source="s"),
        raw_description="",
    )
    result = render(pos, verdict, layout)
    assert "**Missing:**" not in result


def test_matched_label_present_with_non_empty_list(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    result = render(position, green_verdict, layout)
    assert "**Matched:**" in result


def test_missing_label_present_with_non_empty_list(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    result = render(position, green_verdict, layout)
    assert "**Missing:**" in result


# --- job description: hide-if-empty ---


def test_job_description_section_absent_when_raw_description_empty(
    layout: Layout, stub: PositionStub, green_verdict: MatchVerdict
) -> None:
    pos = Position(stub=stub, raw_description="")
    result = render(pos, green_verdict, layout)
    assert "## Job Description" not in result


def test_job_description_section_present_when_raw_description_set(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    result = render(position, green_verdict, layout)
    assert "## Job Description" in result
    assert "Some description here." in result


def test_raw_description_rendered_verbatim(
    layout: Layout, stub: PositionStub, green_verdict: MatchVerdict
) -> None:
    raw = "Line one.\n\nLine two with **bold**."
    pos = Position(stub=stub, raw_description=raw)
    result = render(pos, green_verdict, layout)
    assert raw in result


# --- URL footer ---


def test_card_ends_with_horizontal_rule_and_url(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    result = render(position, green_verdict, layout)
    lines = result.splitlines()
    assert lines[-2] == "---"
    assert lines[-1] == "<https://example.com/job/1>"


def test_url_autolinked_in_footer(
    layout: Layout, stub: PositionStub, green_verdict: MatchVerdict
) -> None:
    pos = Position(stub=stub, raw_description="")
    result = render(pos, green_verdict, layout)
    assert "<https://example.com/job/1>" in result


# --- full card integration: all fields present ---


def test_dense_card_structure(
    layout: Layout, stub: PositionStub, green_verdict: MatchVerdict
) -> None:
    from datetime import date

    pos = Position(
        stub=stub,
        raw_description="Full description.",
        salary="€90 000",
        contract_type="permanent",
        employment_type="full-time",
        work_model="hybrid",
        posted_date=date(2026, 2, 1),
    )
    result = render(pos, green_verdict, layout)

    assert result.startswith("# Acme GmbH · Senior Engineer · Berlin (Hybrid)")
    assert "2026-02-01 · permanent · full-time" in result
    assert "**Salary:** €90 000" in result
    assert "## AI Assessment" in result
    assert "**Matched:**" in result
    assert "**Missing:**" in result
    assert "## Job Description" in result
    assert "Full description." in result
    assert result.endswith("---\n<https://example.com/job/1>\n")


# --- sparse card integration: minimal fields ---


def test_sparse_card_omits_optional_sections(
    layout: Layout, green_verdict: MatchVerdict
) -> None:
    pos = Position(
        stub=PositionStub(url="https://example.com/job/2", title="Dev", source="s"),
        raw_description="",
    )
    result = render(pos, green_verdict, layout)

    assert "## Job Description" not in result
    assert "**Salary:**" not in result
    assert "Unknown Location" in result
    assert result.endswith("---\n<https://example.com/job/2>\n")


# --- render() takes no number parameter ---


def test_render_signature_has_no_number_parameter() -> None:
    import inspect

    sig = inspect.signature(render)
    assert "number" not in sig.parameters
    assert list(sig.parameters.keys()) == ["position", "verdict", "layout"]


# --- all three tiers produce a card ---


def test_green_tier_renders_card(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    result = render(position, green_verdict, layout)
    assert "## AI Assessment" in result
    assert "Strong fit overall." in result


def test_amber_tier_renders_card(
    layout: Layout, position: Position, amber_verdict: MatchVerdict
) -> None:
    result = render(position, amber_verdict, layout)
    assert "## AI Assessment" in result
    assert "Partial fit." in result
    assert "Acme GmbH" in result


def test_red_tier_renders_card(
    layout: Layout, position: Position, red_verdict: MatchVerdict
) -> None:
    result = render(position, red_verdict, layout)
    assert "## AI Assessment" in result
    assert "Poor fit." in result
    assert "Senior Engineer" in result


# --- Rank rendering ---


def test_render_includes_default_rank(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    result = render(position, green_verdict, layout)
    assert "**Rank:** 1" in result


def test_render_reflects_explicit_rank(layout: Layout, position: Position) -> None:
    verdict = MatchVerdict(
        matched=[],
        missing=[],
        summary="ok",
        rank=4,
    )
    result = render(position, verdict, layout)
    assert "**Rank:** 4" in result
