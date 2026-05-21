import textwrap

from application_pipeline.skills_pool import parse


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
