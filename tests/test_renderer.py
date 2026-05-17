from dataclasses import replace

import pytest

from application_pipeline import Layout, MatchTier, MatchVerdict
from application_pipeline.parsers.types import Position, PositionStub
from application_pipeline.renderer import render


@pytest.fixture
def layout() -> Layout:
    return Layout(
        tier_emoji={"green": "🟢", "amber": "🟡", "red": "🔴"},
        tier_color={"green": "#2ea043", "amber": "#d29922", "red": "#da3633"},
        placeholder_groups={"meta": (" · ", ["location", "url"])},
        card_template="## {number}. {company} — {title}  {emoji}\n{meta}\n\n**Matched:** {matched}\n**Missing:** {missing}\n\n{summary}\n\n",
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
        tier=MatchTier.green,
        matched=["Python", "Data Engineering"],
        missing=["Rust"],
        summary="Strong fit overall.",
    )


@pytest.fixture
def amber_verdict() -> MatchVerdict:
    return MatchVerdict(
        tier=MatchTier.amber,
        matched=["Python"],
        missing=["Rust", "Go"],
        summary="Partial fit.",
    )


@pytest.fixture
def red_verdict() -> MatchVerdict:
    return MatchVerdict(
        tier=MatchTier.red,
        matched=[],
        missing=["Rust", "C++"],
        summary="Poor fit.",
    )


# --- Layout has no headline_template attribute ---


def test_layout_has_no_headline_template() -> None:
    assert not hasattr(
        Layout(
            tier_emoji={"green": "🟢", "amber": "🟡", "red": "🔴"},
            tier_color={"green": "#2ea043", "amber": "#d29922", "red": "#da3633"},
            placeholder_groups={},
            card_template="{number}",
        ),
        "headline_template",
    )


# --- tracer bullet: green tier renders card_template ---


def test_green_tier_renders_card(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    result = render(position, green_verdict, 1, layout)

    assert "**Matched:**" in result
    assert "**Missing:**" in result
    assert "Strong fit overall." in result


# --- all tiers render full card ---


def test_amber_tier_renders_card(
    layout: Layout, position: Position, amber_verdict: MatchVerdict
) -> None:
    result = render(position, amber_verdict, 2, layout)

    assert "**Matched:**" in result
    assert "**Missing:**" in result
    assert "Partial fit." in result
    assert "Acme GmbH" in result


def test_red_tier_renders_card(
    layout: Layout, position: Position, red_verdict: MatchVerdict
) -> None:
    result = render(position, red_verdict, 3, layout)

    assert "**Matched:**" in result
    assert "**Missing:**" in result
    assert "Poor fit." in result
    assert "Senior Engineer" in result


# --- stub fields flattened into placeholders ---


def test_stub_fields_available_as_top_level_placeholders(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    result = render(position, green_verdict, 42, layout)

    assert "Acme GmbH" in result
    assert "Senior Engineer" in result
    assert "42" in result


def test_stub_url_available_in_placeholder_group(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    result = render(position, green_verdict, 1, layout)

    assert "https://example.com/job/1" in result


# --- verdict fields substituted ---


def test_matched_list_joined_in_output(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    result = render(position, green_verdict, 1, layout)

    assert "Python, Data Engineering" in result


def test_missing_list_joined_in_output(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    result = render(position, green_verdict, 1, layout)

    assert "Rust" in result


def test_empty_matched_list_renders_empty_list_placeholder(
    layout: Layout, position: Position, red_verdict: MatchVerdict
) -> None:
    simple_layout = Layout(
        tier_emoji=layout.tier_emoji,
        tier_color=layout.tier_color,
        placeholder_groups={},
        
        card_template="{matched}",
        empty_list_placeholder="—",
    )
    result = render(position, red_verdict, 1, simple_layout)

    assert result == "—"


# --- tier-derived fields ---


def test_emoji_derived_from_tier(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    result = render(position, green_verdict, 1, layout)

    assert "🟢" in result


def test_color_available_via_layout(layout: Layout, position: Position) -> None:
    color_layout = Layout(
        tier_emoji=layout.tier_emoji,
        tier_color=layout.tier_color,
        placeholder_groups={},
        
        card_template="{color}",
    )
    verdict = MatchVerdict(tier=MatchTier.amber, matched=[], missing=[], summary="")
    result = render(position, verdict, 1, color_layout)

    assert result == "#d29922"


# --- placeholder groups ---


def test_placeholder_group_collapses_with_separator(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    result = render(position, green_verdict, 1, layout)

    assert "Berlin · <https://example.com/job/1>" in result


def test_placeholder_group_omits_none_values(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    position = replace(position, stub=replace(position.stub, location=None))
    result = render(position, green_verdict, 1, layout)

    assert "<https://example.com/job/1>" in result
    assert "None" not in result
    assert "Berlin" not in result


def test_placeholder_group_all_none_renders_empty(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    simple_layout = Layout(
        tier_emoji=layout.tier_emoji,
        tier_color=layout.tier_color,
        placeholder_groups={"meta": (" · ", ["location"])},
        
        card_template="{meta}",
    )
    position_no_location = replace(position, stub=replace(position.stub, location=None))
    result = render(position_no_location, green_verdict, 1, simple_layout)

    assert result == ""


# --- URL autolink in placeholder groups ---


def test_url_in_placeholder_group_is_autolinked(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    url_layout = Layout(
        tier_emoji=layout.tier_emoji,
        tier_color=layout.tier_color,
        placeholder_groups={"link": (" ", ["url"])},
        
        card_template="{link}",
    )
    result = render(position, green_verdict, 1, url_layout)

    assert result == "<https://example.com/job/1>"


# --- matched_bullets and missing_bullets placeholders ---


def test_matched_bullets_renders_bullet_list(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    bullets_layout = Layout(
        tier_emoji=layout.tier_emoji,
        tier_color=layout.tier_color,
        placeholder_groups={},
        
        card_template="{matched_bullets}",
    )
    result = render(position, green_verdict, 1, bullets_layout)

    assert result == "- Python\n- Data Engineering"


def test_missing_bullets_renders_bullet_list(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    bullets_layout = Layout(
        tier_emoji=layout.tier_emoji,
        tier_color=layout.tier_color,
        placeholder_groups={},
        
        card_template="{missing_bullets}",
    )
    result = render(position, green_verdict, 1, bullets_layout)

    assert result == "- Rust"


def test_empty_matched_bullets_renders_empty_list_placeholder(
    layout: Layout, position: Position, red_verdict: MatchVerdict
) -> None:
    bullets_layout = Layout(
        tier_emoji=layout.tier_emoji,
        tier_color=layout.tier_color,
        placeholder_groups={},
        
        card_template="{matched_bullets}",
        empty_list_placeholder="—",
    )
    result = render(position, red_verdict, 1, bullets_layout)

    assert result == "—"


# --- null company skipped in group ---


def test_null_company_skipped_in_group(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    group_layout = Layout(
        tier_emoji=layout.tier_emoji,
        tier_color=layout.tier_color,
        placeholder_groups={"meta": (" · ", ["company", "url"])},
        
        card_template="{meta}",
    )
    stub_nulls = replace(position.stub, company=None)
    position_nulls = replace(position, stub=stub_nulls)
    result = render(position_nulls, green_verdict, 1, group_layout)

    assert "None" not in result
    assert "<https://example.com/job/1>" in result


# --- number substitution ---


def test_number_substituted_correctly(
    layout: Layout, position: Position, amber_verdict: MatchVerdict
) -> None:
    result = render(position, amber_verdict, 99, layout)

    assert "99." in result


# --- raw_description excluded from placeholders ---


def test_raw_description_not_available_as_template_placeholder(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    raw_desc_layout = Layout(
        tier_emoji=layout.tier_emoji,
        tier_color=layout.tier_color,
        placeholder_groups={},
        
        card_template="{raw_description}",
    )
    with pytest.raises(KeyError):
        render(position, green_verdict, 1, raw_desc_layout)
