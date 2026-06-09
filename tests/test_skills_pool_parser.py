import pathlib
import textwrap

from application_pipeline import triage_skills
from application_pipeline.skills_pool import parse as compat_parse
from application_pipeline.skills_pool import parser as legacy_parser
from application_pipeline.triage_skills import SkillGroup, SkillItem


def test_triage_skills_parse_returns_two_groups_in_file_order() -> None:
    text = textwrap.dedent("""\
        ## Machine Learning
        - Python
        - TensorFlow

        ## Web
        - JavaScript
        - TypeScript
    """)

    result = triage_skills.parse(text)

    assert len(result) == 2
    assert result[0].name == "Machine Learning"
    assert result[1].name == "Web"
    assert [item.name for item in result[0].items] == ["Python", "TensorFlow"]
    assert [item.name for item in result[1].items] == ["JavaScript", "TypeScript"]


def test_triage_skills_parse_parses_group_heading_attrs() -> None:
    text = "## MLE {always, games=high, mle=low}\n- Python\n"

    result = triage_skills.parse(text)

    assert len(result) == 1
    group = result[0]
    assert group.name == "MLE"
    assert group.always is True
    assert group.relevance == {"games": "high", "mle": "low"}


def test_triage_skills_parse_parses_item_always_attr() -> None:
    text = "## Skills\n- Pandas {always}\n"

    result = triage_skills.parse(text)

    item = result[0].items[0]
    assert item.name == "Pandas"
    assert item.always is True


def test_triage_skills_parse_defaults_item_without_attrs() -> None:
    text = "## Skills\n- TensorFlow\n"

    result = triage_skills.parse(text)

    item = result[0].items[0]
    assert item.name == "TensorFlow"
    assert item.always is False


def test_triage_skills_parse_treats_whitespace_variants_equivalently() -> None:
    variants = [
        "## G {always}\n- S {always}\n",
        "## G { always }\n- S { always }\n",
        "## G {always,games=high}\n- S {always}\n",
        "## G {always, games=high}\n- S {always}\n",
    ]

    results = [triage_skills.parse(v) for v in variants]

    for result in results:
        assert result[0].always is True
        assert result[0].items[0].always is True

    assert results[2][0].relevance == {"games": "high"}
    assert results[3][0].relevance == {"games": "high"}


def test_triage_skills_parse_returns_empty_list_for_empty_file() -> None:
    assert triage_skills.parse("") == []


def test_triage_skills_parse_returns_empty_list_without_groups() -> None:
    text = "- Python\n- SQL\n"

    assert triage_skills.parse(text) == []


def test_triage_skills_parse_drops_bullets_before_first_h2() -> None:
    text = textwrap.dedent("""\
        - orphan
        ## Group
        - member
    """)

    result = triage_skills.parse(text)

    assert len(result) == 1
    assert [item.name for item in result[0].items] == ["member"]


def test_triage_skills_parse_ignores_unknown_group_attrs() -> None:
    text = "## G {always, weird-flag, games=medium-high, mle=low}\n"

    result = triage_skills.parse(text)

    group = result[0]
    assert group.always is True
    assert group.relevance == {"mle": "low"}


def test_triage_skills_parse_tolerates_unclosed_item_attrs() -> None:
    text = "## Skills\n- Pandas {always\n"

    result = triage_skills.parse(text)

    item = result[0].items[0]
    assert item.name == "Pandas"
    assert item.always is False


def test_triage_skills_parse_preserves_group_and_item_order() -> None:
    text = textwrap.dedent("""\
        ## A
        - a1
        - a2
        ## B
        - b1
        ## C
        - c1
        - c2
        - c3
    """)

    result = triage_skills.parse(text)

    assert [g.name for g in result] == ["A", "B", "C"]
    assert [item.name for item in result[0].items] == ["a1", "a2"]
    assert [item.name for item in result[1].items] == ["b1"]
    assert [item.name for item in result[2].items] == ["c1", "c2", "c3"]


def test_triage_skills_parse_is_canonical_skill_group_parser_surface() -> None:
    text = "## Backend {always, mle=low}\n- Python {always}\n"

    result = triage_skills.parse(text)

    assert result == [
        SkillGroup(
            name="Backend",
            always=True,
            relevance={"mle": "low"},
            items=[SkillItem(name="Python", always=True)],
        )
    ]


def test_triage_skills_judge_text_preserves_flat_file_order() -> None:
    text = "- Python\n- SQL {always}\n- Go\n"

    result = triage_skills.parse_document(text)

    assert result.judge_text == "- Python\n- SQL\n- Go"


def test_triage_skills_judge_text_flattens_grouped_skills_in_file_order() -> None:
    text = textwrap.dedent("""\
        ## MLE
        - Pandas {always}
        - TensorFlow
        ## Backend
        - Go
    """)

    result = triage_skills.parse_document(text)

    assert result.judge_text == "- Pandas\n- TensorFlow\n- Go"


def test_triage_skills_skill_groups_preserve_authored_group_and_item_order() -> None:
    text = textwrap.dedent("""\
        ## MLE
        - Pandas {always}
        - TensorFlow
        ## Backend
        - Go
        - Python
    """)

    result = triage_skills.parse_document(text)

    assert result.skill_groups == [
        SkillGroup(
            name="MLE",
            always=False,
            relevance={},
            items=[
                SkillItem(name="Pandas", always=True),
                SkillItem(name="TensorFlow", always=False),
            ],
        ),
        SkillGroup(
            name="Backend",
            always=False,
            relevance={},
            items=[
                SkillItem(name="Go", always=False),
                SkillItem(name="Python", always=False),
            ],
        ),
    ]


def test_triage_skills_skill_groups_preserve_group_attrs_without_promoting_item_always() -> (
    None
):
    text = "## Backend {always, mle=high}\n- Go {always}\n- Python\n"

    result = triage_skills.parse_document(text)

    assert result.skill_groups == [
        SkillGroup(
            name="Backend",
            always=True,
            relevance={"mle": "high"},
            items=[
                SkillItem(name="Go", always=True),
                SkillItem(name="Python", always=False),
            ],
        )
    ]


def test_triage_skills_skill_groups_degenerate_for_flat_legacy_bullets() -> None:
    result = triage_skills.parse_document("- Python\n- SQL {always}\n")

    assert result.skill_groups == []


def test_triage_skills_skill_groups_drop_bullets_before_first_h2() -> None:
    text = textwrap.dedent("""\
        - orphan
        ## Backend
        - Go
    """)

    result = triage_skills.parse_document(text)

    assert result.skill_groups == [
        SkillGroup(
            name="Backend",
            always=False,
            relevance={},
            items=[SkillItem(name="Go", always=False)],
        )
    ]


def test_triage_skills_skill_groups_are_empty_for_empty_input() -> None:
    result = triage_skills.parse_document("")

    assert result.skill_groups == []


def test_triage_skills_judge_text_matches_prompt_loader_attribute_tolerance() -> None:
    text = textwrap.dedent("""\
        - Pandas {always
        - NumPy {weird}
        ## Backend {always, mle=low}
        - Go {always, weird}
    """)

    result = triage_skills.parse_document(text)

    assert result.judge_text == "- Pandas {always\n- NumPy\n- Go"


def test_triage_skills_skill_groups_ignore_invalid_always_assignment_but_keep_valid_relevance() -> (
    None
):
    text = "## Backend {always=high, weird-flag, mle=low}\n- Go\n"

    result = triage_skills.parse_document(text)

    assert result.skill_groups == [
        SkillGroup(
            name="Backend",
            always=False,
            relevance={"mle": "low"},
            items=[SkillItem(name="Go", always=False)],
        )
    ]


def test_triage_skills_skill_groups_fall_back_to_defaults_for_unclosed_group_attrs() -> (
    None
):
    text = "## Backend {always, mle=high\n- Go {always}\n"

    result = triage_skills.parse_document(text)

    assert result.skill_groups == [
        SkillGroup(
            name="Backend",
            always=False,
            relevance={},
            items=[SkillItem(name="Go", always=True)],
        )
    ]


def test_triage_skills_load_document_matches_text_parse(
    tmp_path: pathlib.Path,
) -> None:
    text = textwrap.dedent("""\
        ## MLE {always, backend=high}
        - Pandas {always}
        - TensorFlow
        ## Backend
        - Go
    """)
    skills_path = tmp_path / "skills.md"
    skills_path.write_text(text, encoding="utf-8")

    result = triage_skills.load_document(skills_path)

    assert result == triage_skills.parse_document(text)


def test_triage_skills_load_matches_text_parse(tmp_path: pathlib.Path) -> None:
    text = textwrap.dedent("""\
        ## MLE {always, backend=high}
        - Pandas {always}
        - TensorFlow
        ## Backend
        - Go
    """)
    skills_path = tmp_path / "skills.md"
    skills_path.write_text(text, encoding="utf-8")

    result = triage_skills.load(skills_path)

    assert result == triage_skills.parse(text)


def test_triage_skills_load_document_matches_text_parse_for_utf8_bom_file(
    tmp_path: pathlib.Path,
) -> None:
    text = textwrap.dedent("""\
        - Python
        ## Backend
        - Go {always}
    """)
    skills_path = tmp_path / "skills.md"
    skills_path.write_text(text, encoding="utf-8-sig")

    result = triage_skills.load_document(skills_path)

    assert result == triage_skills.parse_document(text)


def test_triage_skills_load_judge_text_flattens_grouped_skills_in_file_order(
    tmp_path: pathlib.Path,
) -> None:
    text = textwrap.dedent("""\
        ## MLE
        - Pandas {always}
        - TensorFlow
        ## Backend
        - Go
    """)
    skills_path = tmp_path / "skills.md"
    skills_path.write_text(text, encoding="utf-8")

    result = triage_skills.load_judge_text(skills_path)

    assert result == "- Pandas\n- TensorFlow\n- Go"


def test_triage_skills_missing_file_yields_empty_views(
    tmp_path: pathlib.Path,
) -> None:
    missing_path = tmp_path / "skills.md"

    document = triage_skills.load_document(missing_path)

    assert document.judge_text == ""
    assert triage_skills.load(missing_path) == []


def test_triage_skills_load_judge_text_missing_file_yields_empty_text(
    tmp_path: pathlib.Path,
) -> None:
    missing_path = tmp_path / "skills.md"

    assert triage_skills.load_judge_text(missing_path) == ""


def test_skills_pool_imports_are_thin_aliases_of_triage_skills() -> None:
    assert compat_parse is triage_skills.parse
    assert legacy_parser.parse is triage_skills.parse
    assert legacy_parser.SkillGroup is triage_skills.SkillGroup
    assert legacy_parser.SkillItem is triage_skills.SkillItem
