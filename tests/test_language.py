from application_pipeline.language import resolve_language
from application_pipeline.parsers.types import Position, PositionStub


def _pos(title: str, raw_description: str, language: str | None = None) -> Position:
    return Position(
        stub=PositionStub(
            url="http://x", title=title, source="test", language=language
        ),
        raw_description=raw_description,
    )


def test_stub_language_returned_directly() -> None:
    assert resolve_language(_pos("Job", "desc", language="de")) == "de"


def test_stub_language_en_returned_directly() -> None:
    assert resolve_language(_pos("Job", "desc", language="en")) == "en"


def test_stub_language_other_returned_directly() -> None:
    assert resolve_language(_pos("Job", "desc", language="other")) == "other"


def test_stub_language_unknown_returned_directly() -> None:
    assert resolve_language(_pos("Job", "desc", language="unknown")) == "unknown"


def test_stub_language_none_detects_german() -> None:
    pos = _pos(
        title="Softwareentwickler gesucht",
        raw_description=(
            "Wir suchen einen erfahrenen Softwareentwickler für unser Team in Hamburg. "
            "Das Unternehmen bietet interessante Projekte und eine gute Bezahlung. "
            "Bewerben Sie sich jetzt mit Ihren vollständigen Unterlagen."
        ),
    )
    assert resolve_language(pos) == "de"


def test_stub_language_none_detects_english() -> None:
    pos = _pos(
        title="Software Engineer wanted",
        raw_description=(
            "We are looking for an experienced software engineer to join our team in London. "
            "The company offers interesting projects and competitive pay. "
            "Apply now with your full application documents."
        ),
    )
    assert resolve_language(pos) == "en"


def test_stub_language_none_undetectable_gives_unknown() -> None:
    assert resolve_language(_pos("123", "456 789 012")) == "unknown"


def test_stub_language_none_non_de_en_gives_other() -> None:
    pos = _pos(
        title="Ingénieur logiciel",
        raw_description=(
            "Nous recherchons un ingénieur logiciel expérimenté pour rejoindre notre équipe. "
            "L'entreprise offre des projets intéressants et une bonne rémunération. "
            "Postulez maintenant avec vos documents complets."
        ),
    )
    assert resolve_language(pos) == "other"
