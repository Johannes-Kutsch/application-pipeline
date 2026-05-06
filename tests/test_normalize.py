from application_pipeline.text import normalize


def test_normalize_collapses_whitespace() -> None:
    assert normalize("  hello   world  ") == "hello world"


def test_normalize_casefolds() -> None:
    assert normalize("Hello World") == "hello world"


def test_normalize_casefold_german_sharp_s() -> None:
    # casefold maps ß → ss, so Straße and Strasse collide
    assert normalize("Straße") == normalize("Strasse")


def test_normalize_none_returns_none() -> None:
    assert normalize(None) is None


def test_normalize_empty_string_returns_none() -> None:
    assert normalize("") is None


def test_normalize_whitespace_only_returns_none() -> None:
    assert normalize("   ") is None


def test_normalize_combines_whitespace_collapse_and_casefold() -> None:
    assert normalize("  Python  Developer  ") == "python developer"
