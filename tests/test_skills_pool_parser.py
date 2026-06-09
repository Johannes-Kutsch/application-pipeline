import textwrap

from application_pipeline import triage_skills
from application_pipeline.skills_pool import parse
from application_pipeline.skills_pool import parser as legacy_parser
from application_pipeline.triage_skills import SkillGroup, SkillItem


def test_two_groups_returned_in_file_order() -> None:
    text = textwrap.dedent("""\
        ## Machine Learning
        - Python
        - TensorFlow

        ## Web
        - JavaScript
        - TypeScript
    """)

    result = parse(text)

    assert len(result) == 2
    assert result[0].name == "Machine Learning"
    assert result[1].name == "Web"
    assert [item.name for item in result[0].items] == ["Python", "TensorFlow"]
    assert [item.name for item in result[1].items] == ["JavaScript", "TypeScript"]


def test_group_heading_attrs_parsed() -> None:
    text = "## MLE {always, games=high, mle=low}\n- Python\n"

    result = parse(text)

    assert len(result) == 1
    group = result[0]
    assert group.name == "MLE"
    assert group.always is True
    assert group.relevance == {"games": "high", "mle": "low"}


def test_item_with_always_attr() -> None:
    text = "## Skills\n- Pandas {always}\n"

    result = parse(text)

    item = result[0].items[0]
    assert item.name == "Pandas"
    assert item.always is True


def test_item_without_attrs_defaults() -> None:
    text = "## Skills\n- TensorFlow\n"

    result = parse(text)

    item = result[0].items[0]
    assert item.name == "TensorFlow"
    assert item.always is False


def test_whitespace_variants_are_equivalent() -> None:
    variants = [
        "## G {always}\n- S {always}\n",
        "## G { always }\n- S { always }\n",
        "## G {always,games=high}\n- S {always}\n",
        "## G {always, games=high}\n- S {always}\n",
    ]

    results = [parse(v) for v in variants]

    for result in results:
        assert result[0].always is True
        assert result[0].items[0].always is True

    assert results[2][0].relevance == {"games": "high"}
    assert results[3][0].relevance == {"games": "high"}


def test_empty_file_yields_empty_list() -> None:
    assert parse("") == []


def test_file_with_no_h2_yields_empty_list() -> None:
    text = "- Python\n- SQL\n"

    assert parse(text) == []


def test_bullets_before_first_h2_are_dropped() -> None:
    text = textwrap.dedent("""\
        - orphan
        ## Group
        - member
    """)

    result = parse(text)

    assert len(result) == 1
    assert [item.name for item in result[0].items] == ["member"]


def test_unknown_group_attrs_ignored() -> None:
    text = "## G {always, weird-flag, games=medium-high, mle=low}\n"

    result = parse(text)

    group = result[0]
    assert group.always is True
    assert group.relevance == {"mle": "low"}


def test_unclosed_brace_yields_defaults_no_exception() -> None:
    text = "## Skills\n- Pandas {always\n"

    result = parse(text)

    item = result[0].items[0]
    assert item.name == "Pandas"
    assert item.always is False


def test_file_order_preserved_across_groups_and_items() -> None:
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

    result = parse(text)

    assert [g.name for g in result] == ["A", "B", "C"]
    assert [item.name for item in result[0].items] == ["a1", "a2"]
    assert [item.name for item in result[1].items] == ["b1"]
    assert [item.name for item in result[2].items] == ["c1", "c2", "c3"]


def test_triage_skills_is_canonical_skill_group_parser_surface() -> None:
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


def test_triage_skills_judge_text_matches_prompt_loader_attribute_tolerance() -> None:
    text = textwrap.dedent("""\
        - Pandas {always
        - NumPy {weird}
        ## Backend {always, mle=low}
        - Go {always, weird}
    """)

    result = triage_skills.parse_document(text)

    assert result.judge_text == "- Pandas {always\n- NumPy\n- Go"


def test_skills_pool_imports_are_thin_aliases_of_triage_skills() -> None:
    assert parse is triage_skills.parse
    assert legacy_parser.parse is triage_skills.parse
    assert legacy_parser.SkillGroup is triage_skills.SkillGroup
    assert legacy_parser.SkillItem is triage_skills.SkillItem
