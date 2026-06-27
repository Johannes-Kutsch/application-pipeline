import textwrap
from pathlib import Path

import pytest

from application_pipeline.content_pool import ContentPoolError, load, parse

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


def test_load_projects_resume_slot_candidates_in_authored_order(pool_tex: Path) -> None:
    pool_tex.write_text(
        textwrap.dedent("""\
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

            % ===== Projekte =====

            %%% ITEM: itemProjectOne
            %%% always: false
            %%% relevance: mle=high
            \\newcommand{\\itemProjectOne}{%
              \\cventry{2024}{One}{}{}{}{First.}%
            }

            %%% ITEM: itemProjectTwo
            %%% always: true
            %%% relevance: games=high
            \\newcommand{\\itemProjectTwo}{%
              \\cventry{2025}{Two}{}{}{}{Second.}%
            }
        """),
        encoding="utf-8",
    )

    document = load(pool_tex)

    assert [
        candidate["name"] for candidate in document.candidates("resume_projekte")
    ] == [
        "itemProjectOne",
        "itemProjectTwo",
    ]


def test_load_maps_jobs_and_education_sections_to_resume_slots(pool_tex: Path) -> None:
    pool_tex.write_text(
        textwrap.dedent("""\
            % ===== Berufserfahrung =====

            %%% ITEM: itemJobFirst
            %%% always: false
            %%% relevance: mle=high
            \\newcommand{\\itemJobFirst}{}

            %%% ITEM: itemJobSecond
            %%% always: true
            %%% relevance: games=medium
            \\newcommand{\\itemJobSecond}{}

            % ===== Ausbildung =====

            %%% ITEM: itemDegreeBachelor
            %%% always: true
            %%% relevance: mle=medium
            \\newcommand{\\itemDegreeBachelor}{}

            %%% ITEM: itemDegreeMaster
            %%% always: true
            %%% relevance: mle=high
            \\newcommand{\\itemDegreeMaster}{}
        """),
        encoding="utf-8",
    )

    document = load(pool_tex)

    assert [
        candidate["name"] for candidate in document.candidates("resume_berufserfahrung")
    ] == ["itemJobFirst", "itemJobSecond"]
    assert [
        candidate["name"] for candidate in document.candidates("resume_ausbildung")
    ] == ["itemDegreeBachelor", "itemDegreeMaster"]


def test_candidates_reject_unknown_and_non_resume_slots(pool_tex: Path) -> None:
    document = load(pool_tex)

    with pytest.raises(ContentPoolError, match="recipient_company"):
        document.candidates("recipient_company")

    with pytest.raises(ContentPoolError, match="resume_unknown"):
        document.candidates("resume_unknown")
