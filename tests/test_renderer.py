import pytest

from application_pipeline import Layout, MatchTier, MatchVerdict
from application_pipeline.parsers.types import Position, PositionStub
from application_pipeline.renderer import render


@pytest.fixture
def layout() -> Layout:
    return Layout(
        tier_emoji={"green": "🟢", "amber": "🟡", "red": "🔴"},
        tier_color={"green": "#2ea043", "amber": "#d29922", "red": "#da3633"},
        placeholder_groups={"meta": (" · ", ["location", "language", "url"])},
        file_header="# Results\n\n",
        card_template="## {number}. {company} — {title}  {emoji}\n{meta}\n\n**Matched:** {matched}\n**Missing:** {missing}\n\n{summary}\n\n",
        headline_template="## {number}. {company} — {title}  {emoji}\n{meta}\n\n",
    )


@pytest.fixture
def stub() -> PositionStub:
    return PositionStub(
        url="https://example.com/job/1",
        title="Senior Engineer",
        source="test-source",
        company="Acme GmbH",
        location="Berlin",
        language="de",
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


# --- tracer bullet: green tier renders card_template ---


def test_green_tier_renders_card(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    result = render(position, green_verdict, 1, layout)

    assert "**Matched:**" in result
    assert "**Missing:**" in result
    assert "Strong fit overall." in result


# --- template selection ---


def test_amber_tier_renders_headline(
    layout: Layout, position: Position, amber_verdict: MatchVerdict
) -> None:
    result = render(position, amber_verdict, 2, layout)

    assert "**Matched:**" not in result
    assert "Acme GmbH" in result


def test_red_tier_renders_headline(
    layout: Layout, position: Position, red_verdict: MatchVerdict
) -> None:
    result = render(position, red_verdict, 3, layout)

    assert "**Matched:**" not in result
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


def test_empty_matched_list_renders_empty_string(
    layout: Layout, position: Position, red_verdict: MatchVerdict
) -> None:
    simple_layout = Layout(
        tier_emoji=layout.tier_emoji,
        tier_color=layout.tier_color,
        placeholder_groups={},
        file_header=layout.file_header,
        card_template="{matched}",
        headline_template="{matched}",
    )
    result = render(position, red_verdict, 1, simple_layout)

    assert result == ""


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
        file_header=layout.file_header,
        card_template="{color}",
        headline_template="{color}",
    )
    verdict = MatchVerdict(tier=MatchTier.amber, matched=[], missing=[], summary="")
    result = render(position, verdict, 1, color_layout)

    assert result == "#d29922"


# --- placeholder groups ---


def test_placeholder_group_collapses_with_separator(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    result = render(position, green_verdict, 1, layout)

    assert "Berlin · de · https://example.com/job/1" in result


def test_placeholder_group_omits_none_values(
    layout: Layout, stub: PositionStub, green_verdict: MatchVerdict
) -> None:
    position = Position(
        stub=PositionStub(
            url=stub.url,
            title=stub.title,
            source=stub.source,
            company=stub.company,
            location=None,
            language=stub.language,
        ),
        raw_description="",
    )
    result = render(position, green_verdict, 1, layout)

    assert "de · https://example.com/job/1" in result
    assert "None" not in result


def test_placeholder_group_all_none_renders_empty(
    layout: Layout, position: Position, green_verdict: MatchVerdict
) -> None:
    simple_layout = Layout(
        tier_emoji=layout.tier_emoji,
        tier_color=layout.tier_color,
        placeholder_groups={"meta": (" · ", ["location"])},
        file_header=layout.file_header,
        card_template="{meta}",
        headline_template="{meta}",
    )
    position_no_location = Position(
        stub=PositionStub(
            url=position.stub.url,
            title=position.stub.title,
            source=position.stub.source,
            location=None,
        ),
        raw_description="",
    )
    result = render(position_no_location, green_verdict, 1, simple_layout)

    assert result == ""


# --- number substitution ---


def test_number_substituted_correctly(
    layout: Layout, position: Position, amber_verdict: MatchVerdict
) -> None:
    result = render(position, amber_verdict, 99, layout)

    assert "99." in result
