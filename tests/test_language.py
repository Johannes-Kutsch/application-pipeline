from application_pipeline.language import LanguageResolution, resolve_language
from application_pipeline.parsers.types import Position, PositionStub


def _pos(title: str, raw_description: str, language: str | None = None) -> Position:
    return Position(
        stub=PositionStub(
            url="http://x",
            title=title,
            source="test",
            language=language,  # type: ignore[arg-type]
        ),
        raw_description=raw_description,
    )


def test_stub_language_de_returns_resolution() -> None:
    r = resolve_language(_pos("Job", "desc", language="de"))
    assert r == LanguageResolution(effective="de", detected="de", source="parser")


def test_stub_language_en_returns_resolution() -> None:
    r = resolve_language(_pos("Job", "desc", language="en"))
    assert r == LanguageResolution(effective="en", detected="en", source="parser")


def test_stub_language_none_detects_german() -> None:
    pos = _pos(
        title="Softwareentwickler gesucht",
        raw_description=(
            "Wir suchen einen erfahrenen Softwareentwickler für unser Team in Hamburg. "
            "Das Unternehmen bietet interessante Projekte und eine gute Bezahlung. "
            "Bewerben Sie sich jetzt mit Ihren vollständigen Unterlagen."
        ),
    )
    r = resolve_language(pos)
    assert r == LanguageResolution(effective="de", detected="de", source="langdetect")


def test_stub_language_none_detects_english() -> None:
    pos = _pos(
        title="Software Engineer wanted",
        raw_description=(
            "We are looking for an experienced software engineer to join our team in London. "
            "The company offers interesting projects and competitive pay. "
            "Apply now with your full application documents."
        ),
    )
    r = resolve_language(pos)
    assert r == LanguageResolution(effective="en", detected="en", source="langdetect")


def test_stub_language_none_undetectable_gives_unknown() -> None:
    r = resolve_language(_pos("123", "456 789 012"))
    assert r == LanguageResolution(
        effective="en", detected="unknown", source="langdetect"
    )


def test_stub_language_none_non_de_en_gives_other() -> None:
    pos = _pos(
        title="Ingénieur logiciel",
        raw_description=(
            "Nous recherchons un ingénieur logiciel expérimenté pour rejoindre notre équipe. "
            "L'entreprise offre des projets intéressants et une bonne rémunération. "
            "Postulez maintenant avec vos documents complets."
        ),
    )
    r = resolve_language(pos)
    assert r == LanguageResolution(
        effective="en", detected="other", source="langdetect"
    )
