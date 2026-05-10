import dataclasses
import pathlib
import re
import textwrap

import pytest

from application_pipeline import Layout, LayoutError, UserSettingsError, load_layout


_MINIMAL_BODY = textwrap.dedent(
    """
    TIER_EMOJI = {"green": "🟢", "amber": "🟡", "red": "🔴"}
    TIER_COLOR = {"green": "#2ea043", "amber": "#d29922", "red": "#da3633"}
    PLACEHOLDER_GROUPS = {"meta": (" · ", ["location", "language", "url"])}
    FILE_HEADER = "# Results\\n"
    CARD_TEMPLATE = "## {number}. {company}\\n"
    HEADLINE_TEMPLATE = "## {number}. {company}\\n"
    """
)


def write_layout(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    path = tmp_path / "layout.py"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


# --- tracer bullet: load returns populated Layout ---


def test_load_returns_populated_layout(tmp_path: pathlib.Path) -> None:
    path = write_layout(tmp_path, _MINIMAL_BODY)

    layout = load_layout(path)

    assert isinstance(layout, Layout)
    assert layout.tier_emoji == {"green": "🟢", "amber": "🟡", "red": "🔴"}
    assert layout.tier_color == {
        "green": "#2ea043",
        "amber": "#d29922",
        "red": "#da3633",
    }
    assert layout.placeholder_groups == {
        "meta": (" · ", ["location", "language", "url"])
    }
    assert layout.file_header == "# Results\n"
    assert layout.card_template == "## {number}. {company}\n"
    assert layout.headline_template == "## {number}. {company}\n"


# --- Layout dataclass properties ---


def test_layout_is_frozen(tmp_path: pathlib.Path) -> None:
    path = write_layout(tmp_path, _MINIMAL_BODY)
    layout = load_layout(path)

    with pytest.raises(dataclasses.FrozenInstanceError):
        layout.file_header = "x"  # type: ignore[misc]


# --- LayoutError hierarchy ---


def test_layout_error_is_user_settings_error() -> None:
    assert issubclass(LayoutError, UserSettingsError)


# --- Missing required fields ---


@pytest.mark.parametrize(
    "missing",
    [
        "TIER_EMOJI",
        "TIER_COLOR",
        "PLACEHOLDER_GROUPS",
        "FILE_HEADER",
        "CARD_TEMPLATE",
        "HEADLINE_TEMPLATE",
    ],
)
def test_load_raises_when_required_field_missing(
    tmp_path: pathlib.Path, missing: str
) -> None:
    lines = [
        line
        for line in textwrap.dedent(_MINIMAL_BODY).splitlines()
        if not line.startswith(missing)
    ]
    path = write_layout(tmp_path, "\n".join(lines))

    with pytest.raises(LayoutError, match=missing):
        load_layout(path)


# --- TIER_EMOJI / TIER_COLOR must cover all three tiers ---


@pytest.mark.parametrize("missing_tier", ["green", "amber", "red"])
def test_load_raises_when_tier_emoji_missing_tier(
    tmp_path: pathlib.Path, missing_tier: str
) -> None:
    tiers = {"green": "🟢", "amber": "🟡", "red": "🔴"}
    del tiers[missing_tier]
    body = (
        f"TIER_EMOJI = {tiers!r}\n"
        'TIER_COLOR = {"green": "#2ea043", "amber": "#d29922", "red": "#da3633"}\n'
        "PLACEHOLDER_GROUPS = {}\n"
        'FILE_HEADER = ""\n'
        'CARD_TEMPLATE = ""\n'
        'HEADLINE_TEMPLATE = ""\n'
    )
    path = write_layout(tmp_path, body)

    with pytest.raises(LayoutError, match="TIER_EMOJI"):
        load_layout(path)


@pytest.mark.parametrize("missing_tier", ["green", "amber", "red"])
def test_load_raises_when_tier_color_missing_tier(
    tmp_path: pathlib.Path, missing_tier: str
) -> None:
    colors = {"green": "#2ea043", "amber": "#d29922", "red": "#da3633"}
    del colors[missing_tier]
    body = (
        'TIER_EMOJI = {"green": "🟢", "amber": "🟡", "red": "🔴"}\n'
        f"TIER_COLOR = {colors!r}\n"
        "PLACEHOLDER_GROUPS = {}\n"
        'FILE_HEADER = ""\n'
        'CARD_TEMPLATE = ""\n'
        'HEADLINE_TEMPLATE = ""\n'
    )
    path = write_layout(tmp_path, body)

    with pytest.raises(LayoutError, match="TIER_COLOR"):
        load_layout(path)


# --- PLACEHOLDER_GROUPS unknown field ---


def test_load_raises_when_placeholder_group_references_unknown_field(
    tmp_path: pathlib.Path,
) -> None:
    body = (
        'TIER_EMOJI = {"green": "🟢", "amber": "🟡", "red": "🔴"}\n'
        'TIER_COLOR = {"green": "#2ea043", "amber": "#d29922", "red": "#da3633"}\n'
        'PLACEHOLDER_GROUPS = {"meta": (" · ", ["location", "not_a_real_field"])}\n'
        'FILE_HEADER = ""\n'
        'CARD_TEMPLATE = ""\n'
        'HEADLINE_TEMPLATE = ""\n'
    )
    path = write_layout(tmp_path, body)

    with pytest.raises(LayoutError, match="not_a_real_field"):
        load_layout(path)


def test_load_accepts_empty_placeholder_groups(tmp_path: pathlib.Path) -> None:
    body = (
        'TIER_EMOJI = {"green": "🟢", "amber": "🟡", "red": "🔴"}\n'
        'TIER_COLOR = {"green": "#2ea043", "amber": "#d29922", "red": "#da3633"}\n'
        "PLACEHOLDER_GROUPS = {}\n"
        'FILE_HEADER = ""\n'
        'CARD_TEMPLATE = ""\n'
        'HEADLINE_TEMPLATE = ""\n'
    )
    path = write_layout(tmp_path, body)

    layout = load_layout(path)

    assert layout.placeholder_groups == {}


# --- File errors propagate as LayoutError ---


def test_load_raises_layout_error_when_file_missing(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "layout.py"

    with pytest.raises(LayoutError, match=re.escape(str(missing))):
        load_layout(missing)


def test_load_raises_layout_error_when_path_is_directory(
    tmp_path: pathlib.Path,
) -> None:
    with pytest.raises(LayoutError, match=re.escape(str(tmp_path))):
        load_layout(tmp_path)


def test_load_wraps_syntax_error(tmp_path: pathlib.Path) -> None:
    path = write_layout(tmp_path, "def broken(:\n")

    with pytest.raises(LayoutError, match=re.escape(str(path.resolve()))):
        load_layout(path)


# --- Second call picks up changes ---


def test_load_picks_up_changed_file_on_second_call(tmp_path: pathlib.Path) -> None:
    path = write_layout(tmp_path, _MINIMAL_BODY)
    first = load_layout(path)
    assert first.file_header == "# Results\n"

    second_body = textwrap.dedent(
        """
        TIER_EMOJI = {"green": "🟢", "amber": "🟡", "red": "🔴"}
        TIER_COLOR = {"green": "#2ea043", "amber": "#d29922", "red": "#da3633"}
        PLACEHOLDER_GROUPS = {}
        FILE_HEADER = "# Changed Header for Second Run\\n"
        CARD_TEMPLATE = "## {number}. {company}\\n"
        HEADLINE_TEMPLATE = "## {number}. {company}\\n"
        """
    )
    path.write_text(second_body, encoding="utf-8")
    second = load_layout(path)

    assert second.file_header == "# Changed Header for Second Run\n"


# --- Unknown top-level names are ignored ---


def test_load_ignores_unknown_top_level_names(tmp_path: pathlib.Path) -> None:
    path = write_layout(tmp_path, _MINIMAL_BODY + "\nEXTRA = 42\n")

    layout = load_layout(path)

    assert layout.file_header == "# Results\n"


# --- EMPTY_LIST_PLACEHOLDER optional field ---


def test_load_reads_empty_list_placeholder_from_module(tmp_path: pathlib.Path) -> None:
    path = write_layout(tmp_path, _MINIMAL_BODY + '\nEMPTY_LIST_PLACEHOLDER = "n/a"\n')

    layout = load_layout(path)

    assert layout.empty_list_placeholder == "n/a"


def test_load_defaults_empty_list_placeholder_when_absent(
    tmp_path: pathlib.Path,
) -> None:
    path = write_layout(tmp_path, _MINIMAL_BODY)

    layout = load_layout(path)

    assert layout.empty_list_placeholder == "—"


# --- TIER_EMOJI / TIER_COLOR must not contain unknown tiers ---


@pytest.mark.parametrize("extra_tier", ["gold", "silver", "unknown"])
def test_load_raises_when_tier_emoji_contains_unknown_tier(
    tmp_path: pathlib.Path, extra_tier: str
) -> None:
    tiers = {"green": "🟢", "amber": "🟡", "red": "🔴", extra_tier: "❓"}
    body = (
        f"TIER_EMOJI = {tiers!r}\n"
        'TIER_COLOR = {"green": "#2ea043", "amber": "#d29922", "red": "#da3633"}\n'
        "PLACEHOLDER_GROUPS = {}\n"
        'FILE_HEADER = ""\n'
        'CARD_TEMPLATE = ""\n'
        'HEADLINE_TEMPLATE = ""\n'
    )
    path = write_layout(tmp_path, body)

    with pytest.raises(LayoutError, match="TIER_EMOJI"):
        load_layout(path)


@pytest.mark.parametrize("extra_tier", ["gold", "silver", "unknown"])
def test_load_raises_when_tier_color_contains_unknown_tier(
    tmp_path: pathlib.Path, extra_tier: str
) -> None:
    colors = {
        "green": "#2ea043",
        "amber": "#d29922",
        "red": "#da3633",
        extra_tier: "#000",
    }
    body = (
        'TIER_EMOJI = {"green": "🟢", "amber": "🟡", "red": "🔴"}\n'
        f"TIER_COLOR = {colors!r}\n"
        "PLACEHOLDER_GROUPS = {}\n"
        'FILE_HEADER = ""\n'
        'CARD_TEMPLATE = ""\n'
        'HEADLINE_TEMPLATE = ""\n'
    )
    path = write_layout(tmp_path, body)

    with pytest.raises(LayoutError, match="TIER_COLOR"):
        load_layout(path)


# --- LayoutError carries structured field + resolved_path ---


def test_layout_error_carries_field_and_resolved_path(tmp_path: pathlib.Path) -> None:
    body = (
        'TIER_COLOR = {"green": "#2ea043", "amber": "#d29922", "red": "#da3633"}\n'
        "PLACEHOLDER_GROUPS = {}\n"
        'FILE_HEADER = ""\n'
        'CARD_TEMPLATE = ""\n'
        'HEADLINE_TEMPLATE = ""\n'
    )
    path = write_layout(tmp_path, body)

    with pytest.raises(LayoutError) as exc_info:
        load_layout(path)

    err = exc_info.value
    assert err.field == "TIER_EMOJI"
    assert err.resolved_path == path.resolve()


# --- Non-groupable fields rejected in PLACEHOLDER_GROUPS ---


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "matched",
        "missing",
        "summary",
        "number",
        "emoji",
        "color",
        "tier",
        "raw_description",
    ],
)
def test_load_raises_when_placeholder_group_references_non_groupable_field(
    tmp_path: pathlib.Path, forbidden_field: str
) -> None:
    body = (
        'TIER_EMOJI = {"green": "🟢", "amber": "🟡", "red": "🔴"}\n'
        'TIER_COLOR = {"green": "#2ea043", "amber": "#d29922", "red": "#da3633"}\n'
        f'PLACEHOLDER_GROUPS = {{"meta": (" · ", ["{forbidden_field}"])}}\n'
        'FILE_HEADER = ""\n'
        'CARD_TEMPLATE = ""\n'
        'HEADLINE_TEMPLATE = ""\n'
    )
    path = write_layout(tmp_path, body)

    with pytest.raises(LayoutError, match=forbidden_field):
        load_layout(path)


def test_load_accepts_matched_bullets_in_placeholder_group(
    tmp_path: pathlib.Path,
) -> None:
    body = (
        'TIER_EMOJI = {"green": "🟢", "amber": "🟡", "red": "🔴"}\n'
        'TIER_COLOR = {"green": "#2ea043", "amber": "#d29922", "red": "#da3633"}\n'
        'PLACEHOLDER_GROUPS = {"skills": ("\\n", ["matched_bullets", "missing_bullets"])}\n'
        'FILE_HEADER = ""\n'
        'CARD_TEMPLATE = ""\n'
        'HEADLINE_TEMPLATE = ""\n'
    )
    path = write_layout(tmp_path, body)

    layout = load_layout(path)

    assert "skills" in layout.placeholder_groups
