import re
from datetime import date

import pytest

from application_pipeline import Layout, MatchVerdict
from application_pipeline.parsers.types import Position, PositionStub
from application_pipeline.renderer import render


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


# --- template-driven output (tracer bullet) ---


def test_card_template_drives_output() -> None:
    layout = Layout(
        placeholder_groups={},
        card_template="{title} at {company}",
    )
    pos = Position(
        stub=PositionStub(
            url="https://example.com/job/1",
            title="Senior Engineer",
            source="s",
            company="Acme",
        ),
        raw_description="",
    )
    verdict = MatchVerdict(matched=[], missing=[], summary="Good.")
    result = render(pos, verdict, layout)
    assert result == "Senior Engineer at Acme"


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
    layout = Layout(
        placeholder_groups={
            "title_line": (" · ", ["company", "title", "location_segment"])
        },
        card_template="# {title_line}",
    )
    result = render(position, green_verdict, layout)
    assert result == "# Acme GmbH · Senior Engineer · Berlin"
    assert "<span" not in result
    assert "🟢" not in result


def test_card_h1_contains_no_number_prefix(
    position: Position, green_verdict: MatchVerdict
) -> None:
    layout = Layout(
        placeholder_groups={
            "title_line": (" · ", ["company", "title", "location_segment"])
        },
        card_template="# {title_line}",
    )
    result = render(position, green_verdict, layout)
    first_line = result.splitlines()[0]
    assert not re.match(r".*\d+\.", first_line)


# --- location_segment rules ---


def test_location_segment_location_only_when_work_model_none(
    green_verdict: MatchVerdict,
) -> None:
    layout = Layout(placeholder_groups={}, card_template="{location_segment}")
    pos = Position(
        stub=PositionStub(
            url="https://example.com/job/1", title="Dev", source="s", location="Munich"
        ),
        raw_description="",
        work_model=None,
    )
    assert render(pos, green_verdict, layout) == "Munich"


def test_location_segment_location_only_when_work_model_on_site(
    green_verdict: MatchVerdict,
) -> None:
    layout = Layout(placeholder_groups={}, card_template="{location_segment}")
    pos = Position(
        stub=PositionStub(
            url="https://example.com/job/1", title="Dev", source="s", location="Munich"
        ),
        raw_description="",
        work_model="on-site",
    )
    result = render(pos, green_verdict, layout)
    assert result == "Munich"
    assert "Hybrid" not in result
    assert "Remote" not in result


def test_location_segment_hybrid_appended(green_verdict: MatchVerdict) -> None:
    layout = Layout(placeholder_groups={}, card_template="{location_segment}")
    pos = Position(
        stub=PositionStub(
            url="https://example.com/job/1", title="Dev", source="s", location="Munich"
        ),
        raw_description="",
        work_model="hybrid",
    )
    assert render(pos, green_verdict, layout) == "Munich (Hybrid)"


def test_location_segment_remote_appended(green_verdict: MatchVerdict) -> None:
    layout = Layout(placeholder_groups={}, card_template="{location_segment}")
    pos = Position(
        stub=PositionStub(
            url="https://example.com/job/1", title="Dev", source="s", location="Munich"
        ),
        raw_description="",
        work_model="remote",
    )
    assert render(pos, green_verdict, layout) == "Munich (Remote)"


def test_location_segment_empty_when_location_none_work_model_none(
    green_verdict: MatchVerdict,
) -> None:
    layout = Layout(placeholder_groups={}, card_template="{location_segment}")
    pos = Position(
        stub=PositionStub(
            url="https://example.com/job/1", title="Dev", source="s", location=None
        ),
        raw_description="",
        work_model=None,
    )
    assert render(pos, green_verdict, layout) == ""


def test_location_segment_hybrid_only_when_location_none(
    green_verdict: MatchVerdict,
) -> None:
    layout = Layout(placeholder_groups={}, card_template="{location_segment}")
    pos = Position(
        stub=PositionStub(
            url="https://example.com/job/1", title="Dev", source="s", location=None
        ),
        raw_description="",
        work_model="hybrid",
    )
    assert render(pos, green_verdict, layout) == "(Hybrid)"


def test_location_segment_remote_only_when_location_none(
    green_verdict: MatchVerdict,
) -> None:
    layout = Layout(placeholder_groups={}, card_template="{location_segment}")
    pos = Position(
        stub=PositionStub(
            url="https://example.com/job/1", title="Dev", source="s", location=None
        ),
        raw_description="",
        work_model="remote",
    )
    assert render(pos, green_verdict, layout) == "(Remote)"


def test_location_segment_dropped_from_group_when_location_none_work_model_none(
    green_verdict: MatchVerdict,
) -> None:
    layout = Layout(
        placeholder_groups={"title_line": (" · ", ["title", "location_segment"])},
        card_template="{title_line}",
    )
    pos = Position(
        stub=PositionStub(
            url="https://example.com/job/1", title="Dev", source="s", location=None
        ),
        raw_description="",
        work_model=None,
    )
    assert render(pos, green_verdict, layout) == "Dev"


# --- null policy: None scalar → "" ---


def test_none_scalar_renders_as_empty_string(green_verdict: MatchVerdict) -> None:
    layout = Layout(placeholder_groups={}, card_template="{salary}")
    pos = Position(
        stub=PositionStub(url="https://example.com/job/1", title="Dev", source="s"),
        raw_description="",
        salary=None,
    )
    assert render(pos, green_verdict, layout) == ""


def test_none_not_rendered_as_literal_none(green_verdict: MatchVerdict) -> None:
    layout = Layout(placeholder_groups={}, card_template="{salary}|{contract_type}")
    pos = Position(
        stub=PositionStub(url="https://example.com/job/1", title="Dev", source="s"),
        raw_description="",
    )
    assert "None" not in render(pos, green_verdict, layout)


# --- null policy: empty list → "" ---


def test_empty_matched_list_renders_as_empty_string(
    green_verdict: MatchVerdict,
) -> None:
    layout = Layout(placeholder_groups={}, card_template="{matched}")
    pos = Position(
        stub=PositionStub(url="https://example.com/job/1", title="Dev", source="s"),
        raw_description="",
    )
    verdict = MatchVerdict(matched=[], missing=[], summary="ok.")
    assert render(pos, verdict, layout) == ""


def test_empty_missing_list_renders_as_empty_string(
    green_verdict: MatchVerdict,
) -> None:
    layout = Layout(placeholder_groups={}, card_template="{missing}")
    pos = Position(
        stub=PositionStub(url="https://example.com/job/1", title="Dev", source="s"),
        raw_description="",
    )
    verdict = MatchVerdict(matched=[], missing=[], summary="ok.")
    assert render(pos, verdict, layout) == ""


def test_matched_comma_joined_when_non_empty(green_verdict: MatchVerdict) -> None:
    layout = Layout(placeholder_groups={}, card_template="{matched}")
    pos = Position(
        stub=PositionStub(url="https://example.com/job/1", title="Dev", source="s"),
        raw_description="",
    )
    assert render(pos, green_verdict, layout) == "Python, Data Engineering"


def test_missing_comma_joined_when_non_empty(green_verdict: MatchVerdict) -> None:
    layout = Layout(placeholder_groups={}, card_template="{missing}")
    pos = Position(
        stub=PositionStub(url="https://example.com/job/1", title="Dev", source="s"),
        raw_description="",
    )
    assert render(pos, green_verdict, layout) == "Rust"


# --- PLACEHOLDER_GROUPS: mixed Some/None members ---


def test_group_with_mixed_none_members_joins_non_none(
    green_verdict: MatchVerdict,
) -> None:
    layout = Layout(
        placeholder_groups={
            "meta_line": (" · ", ["contract_type", "employment_type", "salary"])
        },
        card_template="{meta_line}",
    )
    pos = Position(
        stub=PositionStub(url="https://example.com/job/1", title="Dev", source="s"),
        raw_description="",
        contract_type="permanent",
        employment_type=None,
        salary=None,
    )
    assert render(pos, green_verdict, layout) == "permanent"


def test_group_with_all_none_members_renders_empty_string(
    green_verdict: MatchVerdict,
) -> None:
    layout = Layout(
        placeholder_groups={"meta_line": (" · ", ["contract_type", "employment_type"])},
        card_template="{meta_line}",
    )
    pos = Position(
        stub=PositionStub(url="https://example.com/job/1", title="Dev", source="s"),
        raw_description="",
        contract_type=None,
        employment_type=None,
    )
    assert render(pos, green_verdict, layout) == ""


def test_group_joins_all_present_members(green_verdict: MatchVerdict) -> None:
    layout = Layout(
        placeholder_groups={
            "meta_line": (" · ", ["posted_date", "contract_type", "employment_type"])
        },
        card_template="{meta_line}",
    )
    pos = Position(
        stub=PositionStub(url="https://example.com/job/1", title="Dev", source="s"),
        raw_description="",
        posted_date=date(2026, 3, 1),
        contract_type="freelance",
        employment_type="part-time",
    )
    assert render(pos, green_verdict, layout) == "2026-03-01 · freelance · part-time"


# --- salary ---


def test_salary_renders_when_set(green_verdict: MatchVerdict) -> None:
    layout = Layout(placeholder_groups={}, card_template="{salary}")
    pos = Position(
        stub=PositionStub(url="https://example.com/job/1", title="Dev", source="s"),
        raw_description="",
        salary="€80 000",
    )
    assert render(pos, green_verdict, layout) == "€80 000"


# --- summary ---


def test_summary_rendered(position: Position, green_verdict: MatchVerdict) -> None:
    layout = Layout(placeholder_groups={}, card_template="{summary}")
    assert render(position, green_verdict, layout) == "Strong fit overall."


# --- rank ---


def test_rank_rendered(position: Position, green_verdict: MatchVerdict) -> None:
    layout = Layout(placeholder_groups={}, card_template="{rank}")
    assert render(position, green_verdict, layout) == "1"


def test_render_reflects_explicit_rank(position: Position) -> None:
    layout = Layout(placeholder_groups={}, card_template="{rank}")
    verdict = MatchVerdict(matched=[], missing=[], summary="ok", rank=4)
    assert render(position, verdict, layout) == "4"


# --- url ---


def test_url_rendered(position: Position, green_verdict: MatchVerdict) -> None:
    layout = Layout(placeholder_groups={}, card_template="<{url}>")
    assert render(position, green_verdict, layout) == "<https://example.com/job/1>"


# --- raw_description ---


def test_raw_description_rendered_verbatim(
    stub: PositionStub, green_verdict: MatchVerdict
) -> None:
    raw = "Line one.\n\nLine two with **bold**."
    layout = Layout(placeholder_groups={}, card_template="{raw_description}")
    pos = Position(stub=stub, raw_description=raw)
    assert render(pos, green_verdict, layout) == raw


# --- typo in CARD_TEMPLATE raises KeyError ---


def test_typo_in_card_template_raises_key_error(
    position: Position, green_verdict: MatchVerdict
) -> None:
    layout = Layout(placeholder_groups={}, card_template="{titel}")
    with pytest.raises(KeyError):
        render(position, green_verdict, layout)


# --- render() takes no number parameter ---


def test_render_signature_has_no_number_parameter() -> None:
    import inspect

    sig = inspect.signature(render)
    assert "number" not in sig.parameters
    assert list(sig.parameters.keys()) == ["position", "verdict", "layout"]


# --- all three verdict types produce a card ---


def test_green_verdict_renders_summary(
    position: Position, green_verdict: MatchVerdict
) -> None:
    layout = Layout(placeholder_groups={}, card_template="{summary}")
    assert render(position, green_verdict, layout) == "Strong fit overall."


def test_amber_verdict_renders_summary(
    position: Position, amber_verdict: MatchVerdict
) -> None:
    layout = Layout(placeholder_groups={}, card_template="{title}|{summary}")
    result = render(position, amber_verdict, layout)
    assert "Partial fit." in result
    assert "Senior Engineer" in result


def test_red_verdict_renders_summary(
    position: Position, red_verdict: MatchVerdict
) -> None:
    layout = Layout(placeholder_groups={}, card_template="{title}|{summary}")
    result = render(position, red_verdict, layout)
    assert "Poor fit." in result
    assert "Senior Engineer" in result


# --- full card integration: dense ---

_DENSE_TEMPLATE = """\
# {rank}: {title_line}

{meta_line}

## AI Assessment

{summary}

{matched}

## Job Description

{raw_description}

---
<{url}>"""


def test_dense_card_renders_all_fields(
    stub: PositionStub, green_verdict: MatchVerdict
) -> None:
    layout = Layout(
        placeholder_groups={
            "title_line": (" · ", ["company", "title", "location_segment"]),
            "meta_line": (" · ", ["posted_date", "contract_type", "employment_type"]),
        },
        card_template=_DENSE_TEMPLATE,
    )
    pos = Position(
        stub=stub,
        raw_description="Full description.",
        contract_type="permanent",
        employment_type="full-time",
        work_model="hybrid",
        posted_date=date(2026, 2, 1),
    )
    result = render(pos, green_verdict, layout)

    assert "# 1: Acme GmbH · Senior Engineer · Berlin (Hybrid)" in result
    assert "2026-02-01 · permanent · full-time" in result
    assert "Strong fit overall." in result
    assert "Python, Data Engineering" in result
    assert "Full description." in result
    assert result.endswith("---\n<https://example.com/job/1>")


# --- sparse card integration: minimal fields ---


def test_sparse_card_renders_without_error(green_verdict: MatchVerdict) -> None:
    layout = Layout(
        placeholder_groups={
            "title_line": (" · ", ["company", "title", "location_segment"]),
            "meta_line": (" · ", ["salary", "posted_date", "contract_type"]),
        },
        card_template="# {title_line}\n{meta_line}\n{summary}\n---\n<{url}>",
    )
    pos = Position(
        stub=PositionStub(url="https://example.com/job/2", title="Dev", source="s"),
        raw_description="",
    )
    result = render(pos, green_verdict, layout)

    assert result.startswith("# Dev")
    assert "None" not in result
    assert result.endswith("---\n<https://example.com/job/2>")
