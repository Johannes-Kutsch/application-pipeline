import dataclasses
import pathlib
import re
import sys
import textwrap
from unittest.mock import patch

import pytest

from application_pipeline import Layout, LayoutError, UserSettingsError, load_layout


_MINIMAL_BODY = textwrap.dedent(
    """
    PLACEHOLDER_GROUPS = {"meta": (" · ", ["location", "url"])}
    CARD_TEMPLATE = "## {rank}. {company}\\n"
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
    assert layout.placeholder_groups == {"meta": (" · ", ["location", "url"])}
    assert layout.card_template == "## {rank}. {company}\n"


# --- Layout dataclass properties ---


def test_layout_is_frozen(tmp_path: pathlib.Path) -> None:
    path = write_layout(tmp_path, _MINIMAL_BODY)
    layout = load_layout(path)

    with pytest.raises(dataclasses.FrozenInstanceError):
        layout.card_template = "x"  # type: ignore[misc]


# --- LayoutError hierarchy ---


def test_layout_error_is_user_settings_error() -> None:
    assert issubclass(LayoutError, UserSettingsError)


# --- Missing required fields ---


@pytest.mark.parametrize(
    "missing",
    [
        "PLACEHOLDER_GROUPS",
        "CARD_TEMPLATE",
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


# --- PLACEHOLDER_GROUPS unknown field ---


def test_load_raises_when_placeholder_group_references_unknown_field(
    tmp_path: pathlib.Path,
) -> None:
    body = (
        'PLACEHOLDER_GROUPS = {"meta": (" · ", ["location", "not_a_real_field"])}\n'
        'CARD_TEMPLATE = ""\n'
    )
    path = write_layout(tmp_path, body)

    with pytest.raises(LayoutError, match="not_a_real_field"):
        load_layout(path)


def test_load_accepts_empty_placeholder_groups(tmp_path: pathlib.Path) -> None:
    body = 'PLACEHOLDER_GROUPS = {}\nCARD_TEMPLATE = ""\n'
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
    assert first.card_template == "## {rank}. {company}\n"

    second_body = textwrap.dedent(
        """
        PLACEHOLDER_GROUPS = {}
        CARD_TEMPLATE = "## {rank}. {company}\\n"
        """
    )
    path.write_text(second_body, encoding="utf-8")
    second = load_layout(path)

    assert second.card_template == "## {rank}. {company}\n"


# --- Unknown top-level names are ignored ---


def test_load_ignores_unknown_top_level_names(tmp_path: pathlib.Path) -> None:
    path = write_layout(tmp_path, _MINIMAL_BODY + "\nEXTRA = 42\n")

    layout = load_layout(path)

    assert isinstance(layout, Layout)


# --- HEADLINE_TEMPLATE is optional and silently ignored ---


def test_load_succeeds_when_headline_template_absent(tmp_path: pathlib.Path) -> None:
    path = write_layout(tmp_path, _MINIMAL_BODY)

    layout = load_layout(path)

    assert isinstance(layout, Layout)
    assert not hasattr(layout, "headline_template")


def test_load_succeeds_when_headline_template_present(tmp_path: pathlib.Path) -> None:
    path = write_layout(
        tmp_path, _MINIMAL_BODY + '\nHEADLINE_TEMPLATE = "## {number}. {company}\\n"\n'
    )

    layout = load_layout(path)

    assert isinstance(layout, Layout)
    assert not hasattr(layout, "headline_template")


# --- LayoutError carries structured field + resolved_path ---


def test_layout_error_carries_field_and_resolved_path(tmp_path: pathlib.Path) -> None:
    body = 'CARD_TEMPLATE = ""\n'
    path = write_layout(tmp_path, body)

    with pytest.raises(LayoutError) as exc_info:
        load_layout(path)

    err = exc_info.value
    assert err.field == "PLACEHOLDER_GROUPS"
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
        f'PLACEHOLDER_GROUPS = {{"meta": (" · ", ["{forbidden_field}"])}}\n'
        'CARD_TEMPLATE = ""\n'
    )
    path = write_layout(tmp_path, body)

    with pytest.raises(LayoutError, match=forbidden_field):
        load_layout(path)


@pytest.mark.parametrize("retired_field", ["matched_bullets", "missing_bullets"])
def test_load_raises_for_matched_missing_bullets_in_placeholder_group(
    tmp_path: pathlib.Path, retired_field: str
) -> None:
    body = (
        f'PLACEHOLDER_GROUPS = {{"skills": ("\\n", ["{retired_field}"])}}\n'
        'CARD_TEMPLATE = ""\n'
    )
    path = write_layout(tmp_path, body)

    with pytest.raises(LayoutError, match=retired_field):
        load_layout(path)


def test_load_accepts_location_segment_in_placeholder_group(
    tmp_path: pathlib.Path,
) -> None:
    body = (
        'PLACEHOLDER_GROUPS = {"title_line": (" · ", ["title", "location_segment"])}\n'
        'CARD_TEMPLATE = "{title_line}"\n'
    )
    path = write_layout(tmp_path, body)

    layout = load_layout(path)

    assert "title_line" in layout.placeholder_groups


# --- Smoke-test: valid layout passes ---


def test_valid_layout_passes_smoke_test(tmp_path: pathlib.Path) -> None:
    path = write_layout(tmp_path, _MINIMAL_BODY)

    layout = load_layout(path)

    assert isinstance(layout, Layout)


# --- Retired identifier detection ---


def test_load_raises_for_tier_emoji_module_variable(tmp_path: pathlib.Path) -> None:
    body = (
        'TIER_EMOJI = {"green": "✅", "amber": "⚠️", "red": "❌"}\n'
        "PLACEHOLDER_GROUPS = {}\n"
        'CARD_TEMPLATE = "## {title}\\n"\n'
    )
    path = write_layout(tmp_path, body)

    with pytest.raises(LayoutError) as exc_info:
        load_layout(path)

    assert "tier_emoji" in str(exc_info.value)


def test_load_raises_for_tier_color_module_variable(tmp_path: pathlib.Path) -> None:
    body = (
        'TIER_COLOR = {"green": "#00ff00", "amber": "#ffaa00", "red": "#ff0000"}\n'
        "PLACEHOLDER_GROUPS = {}\n"
        'CARD_TEMPLATE = "## {title}\\n"\n'
    )
    path = write_layout(tmp_path, body)

    with pytest.raises(LayoutError) as exc_info:
        load_layout(path)

    assert "tier_color" in str(exc_info.value)


@pytest.mark.parametrize("placeholder", ["emoji", "color", "tier"])
def test_load_raises_for_retired_card_template_placeholder(
    tmp_path: pathlib.Path, placeholder: str
) -> None:
    body = (
        "PLACEHOLDER_GROUPS = {}\n"
        f'CARD_TEMPLATE = "## {{title}} {{{placeholder}}}\\n"\n'
    )
    path = write_layout(tmp_path, body)

    with pytest.raises(LayoutError) as exc_info:
        load_layout(path)

    assert placeholder in str(exc_info.value)


# --- LayoutError in __main__._FATAL produces a failure artifact ---


def test_layout_error_produces_failure_artifact_via_main(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from application_pipeline.__main__ import main

    config_path = tmp_path / "config.yaml"
    config_path.touch()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["prog", str(config_path)])

    layout_err = LayoutError(
        "smoke-test failed for dense × green",
        resolved_path=config_path,
    )

    with (
        patch("application_pipeline.__main__.RunLog"),
        patch("application_pipeline.__main__.run", side_effect=layout_err),
        patch("application_pipeline.__main__.current_stage") as mock_stage,
        pytest.raises(SystemExit) as exc_info,
    ):
        mock_stage.get.return_value = "load-layout"
        main()

    assert exc_info.value.code == 1
    artifacts = list((tmp_path / "failures").glob("*.md"))
    assert len(artifacts) == 1
