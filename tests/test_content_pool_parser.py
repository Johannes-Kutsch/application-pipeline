import textwrap
from pathlib import Path

import pytest

from application_pipeline.content_pool import ContentPoolError, parse

_FIXTURE = textwrap.dedent("""\
    % ===== Berufserfahrung =====

    %%% ITEM: itemJobExample
    %%% always: false
    %%% group: example
    %%% relevance: mle=high, games=medium
    \\newcommand{\\itemJobExample}{%
      \\cventry{2020--2022}{Engineer}{Acme}{}{}{Body.}%
    }

    % ===== Ausbildung =====

    %%% ITEM: itemDegreeMaster
    %%% always: true
    %%% relevance: mle=high, games=high
    \\newcommand{\\itemDegreeMaster}{%
      \\cventry{2018--2020}{M.Sc.}{University}{}{}{Thesis.}%
    }
""")


@pytest.fixture
def pool_tex(tmp_path: Path) -> Path:
    p = tmp_path / "content_pool.tex"
    p.write_text(_FIXTURE, encoding="utf-8")
    return p


def test_parse_returns_correct_shape(pool_tex: Path) -> None:
    result = parse(pool_tex)

    assert set(result.keys()) == {"itemJobExample", "itemDegreeMaster"}
    item = result["itemJobExample"]
    assert item["section"] == "Berufserfahrung"
    assert item["always"] is False
    assert item["group"] == "example"
    assert item["relevance"] == {"mle": "high", "games": "medium"}


def test_always_is_parsed_as_bool(pool_tex: Path) -> None:
    result = parse(pool_tex)
    assert result["itemJobExample"]["always"] is False
    assert result["itemDegreeMaster"]["always"] is True


def test_group_omitted_defaults_to_none(pool_tex: Path) -> None:
    result = parse(pool_tex)
    assert result["itemDegreeMaster"]["group"] is None


def test_section_derived_from_block_header(pool_tex: Path) -> None:
    result = parse(pool_tex)
    assert result["itemJobExample"]["section"] == "Berufserfahrung"
    assert result["itemDegreeMaster"]["section"] == "Ausbildung"


def test_malformed_relevance_raises_named_error(tmp_path: Path) -> None:
    bad = textwrap.dedent("""\
        % ===== Projekte =====

        %%% ITEM: itemProjectBad
        %%% always: false
        %%% relevance: mle-high
        \\newcommand{\\itemProjectBad}{}
    """)
    p = tmp_path / "content_pool.tex"
    p.write_text(bad, encoding="utf-8")

    with pytest.raises(ContentPoolError, match="itemProjectBad"):
        parse(p)
