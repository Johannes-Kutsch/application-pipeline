import textwrap
from pathlib import Path

import pytest

from application_pipeline.cover_patterns import (
    CoverPattern,
    CoverPatternError,
    CoverPatternLibrary,
    load,
    load_library,
    parse,
    parse_library,
)
from application_pipeline.cv_slot_contract import COVER_PARAGRAPH_PATTERN_SLOTS

_COVER_PATTERNS_TEMPLATE = Path(
    "src/application_pipeline/templates/application-pipeline/user-info/cv/cover-patterns.md"
)


def _pattern_block(
    name: str,
    slot: str,
    *,
    argument_type: str = "resonance",
    use_when: str = "The listing's product surface matches a long-running motivation.",
    placeholders: str = "Musterfirma, Musterprodukt, Musterprojekt",
    why_it_works: str = "It ties employer context to concrete candidate evidence.",
    text: str = (
        "Bei Musterfirma reizt mich besonders, dass Musterprodukt ein Problem "
        "adressiert, das ich in Musterprojekt bereits aus der Builder-"
        "Perspektive durchdrungen habe. Gerade diese Naehe zwischen "
        "Produktproblem und Umsetzungserfahrung macht den Wechsel fuer mich "
        "plausibel."
    ),
) -> str:
    header = textwrap.dedent(
        f"""\
        ## {name}
        - slot: {slot}
        - argument_type: {argument_type}
        - use_when: {use_when}
        - placeholders: {placeholders}
        - why_it_works: {why_it_works}
        """
    )
    return f"{header}\n{text}\n"


def test_cover_pattern_library_parse_returns_patterns_in_authored_order() -> None:
    library = CoverPatternLibrary.parse(
        "\n\n".join(
            [
                _pattern_block("Intro Pattern", "cover_intro"),
                _pattern_block(
                    "Closing Pattern",
                    "cover_closing",
                    argument_type="closing",
                    use_when="The role is a strong match and the close should stay direct.",
                    placeholders="Musterfirma, Musterrolle",
                    why_it_works="It closes with clear intent and references the role directly.",
                    text=(
                        "Deshalb moechte ich meine Erfahrung bei Musterfirma in "
                        "der Musterrolle wirksam einbringen. Ueber ein Gespraech "
                        "dazu, wie ich den Beitrag konkret leisten kann, freue "
                        "ich mich."
                    ),
                ),
            ]
        )
    )

    assert library.all_patterns() == [
        CoverPattern(
            name="Intro Pattern",
            slot="cover_intro",
            argument_type="resonance",
            use_when="The listing's product surface matches a long-running motivation.",
            placeholders=("Musterfirma", "Musterprodukt", "Musterprojekt"),
            why_it_works="It ties employer context to concrete candidate evidence.",
            text=(
                "Bei Musterfirma reizt mich besonders, dass Musterprodukt ein "
                "Problem adressiert, das ich in Musterprojekt bereits aus der "
                "Builder-Perspektive durchdrungen habe. Gerade diese Naehe "
                "zwischen Produktproblem und Umsetzungserfahrung macht den "
                "Wechsel fuer mich plausibel."
            ),
        ),
        CoverPattern(
            name="Closing Pattern",
            slot="cover_closing",
            argument_type="closing",
            use_when="The role is a strong match and the close should stay direct.",
            placeholders=("Musterfirma", "Musterrolle"),
            why_it_works="It closes with clear intent and references the role directly.",
            text=(
                "Deshalb moechte ich meine Erfahrung bei Musterfirma in der "
                "Musterrolle wirksam einbringen. Ueber ein Gespraech dazu, wie "
                "ich den Beitrag konkret leisten kann, freue ich mich."
            ),
        ),
    ]


@pytest.mark.parametrize("seed", [None, "", "   \n"])
def test_cover_pattern_library_load_returns_empty_library_for_missing_or_blank_files(
    seed: str | None, tmp_path: Path
) -> None:
    path = tmp_path / "cover-patterns.md"
    if seed is not None:
        path.write_text(seed)

    assert CoverPatternLibrary.load(path) == CoverPatternLibrary()


def test_cover_pattern_library_load_projects_patterns_by_slot_in_authored_order(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cover-patterns.md"
    path.write_text(
        "\n\n".join(
            [
                _pattern_block("Intro Pattern", "cover_intro"),
                _pattern_block(
                    "Fit Pattern",
                    "cover_fit",
                    argument_type="capability",
                    placeholders="Musterfirma, Musterrolle",
                    text=(
                        "Bei Musterfirma kann ich fuer die Musterrolle "
                        "belastbare Erfahrung direkt nutzbar machen. Diese "
                        "Verantwortung habe ich bereits konkret getragen und "
                        "moechte sie weiter vertiefen."
                    ),
                    why_it_works="It ties the role to demonstrated evidence.",
                    use_when="The role maps directly to prior evidence.",
                ),
                _pattern_block(
                    "Second Intro Pattern",
                    "cover_intro",
                    placeholders="Musterfirma, Musterdomäne",
                    use_when="The domain is compelling for a second intro variant.",
                    why_it_works="It keeps the intro specific to the employer domain.",
                    text=(
                        "Bei Musterfirma reizt mich besonders die Arbeit in der "
                        "Musterdomäne. Diese Verbindung habe ich bereits konkret "
                        "erlebt und moechte sie dort weiter ausbauen."
                    ),
                ),
            ]
        )
    )

    library = CoverPatternLibrary.load(path)

    assert [pattern.name for pattern in library.patterns_for_slot("cover_intro")] == [
        "Intro Pattern",
        "Second Intro Pattern",
    ]
    assert library.patterns_for_slot("cover_closing") == []


@pytest.mark.parametrize(
    ("slot", "message"),
    [
        (
            "opening",
            "cover slot projection requires a cover paragraph slot, got: opening",
        ),
        (
            "resume_projekte",
            "cover slot projection requires a cover paragraph slot, got: resume_projekte",
        ),
        ("unknown_slot", "unknown cover slot: unknown_slot"),
    ],
)
def test_cover_pattern_library_rejects_non_cover_or_unknown_projection_slots(
    slot: str, message: str
) -> None:
    with pytest.raises(CoverPatternError, match=message):
        CoverPatternLibrary().patterns_for_slot(slot)


@pytest.mark.parametrize("slot", COVER_PARAGRAPH_PATTERN_SLOTS)
def test_cover_pattern_library_accepts_cover_slot_contract_for_projection(
    slot: str,
) -> None:
    assert CoverPatternLibrary().patterns_for_slot(slot) == []


def test_parse_and_load_remain_list_compatibility_wrappers(
    tmp_path: Path,
) -> None:
    text = _pattern_block(
        "Compat Pattern",
        "cover_closing",
        argument_type="closing",
        placeholders="Musterfirma, Musterrolle",
        use_when="The role is a strong match and the close should stay direct.",
        why_it_works="It closes with clear intent and references the role directly.",
        text=(
            "Deshalb moechte ich meine Erfahrung bei Musterfirma in der "
            "Musterrolle wirksam einbringen. Ueber ein Gespraech dazu, wie ich "
            "den Beitrag konkret leisten kann, freue ich mich."
        ),
    )
    path = tmp_path / "cover-patterns.md"
    path.write_text(text)

    assert parse(text) == parse_library(text).all_patterns()
    assert load(path) == load_library(path).all_patterns()


@pytest.mark.parametrize("loader_name", ["parse_library", "load_library"])
def test_cover_pattern_library_builders_preserve_cover_pattern_failures(
    loader_name: str, tmp_path: Path
) -> None:
    text = _pattern_block(
        "Invalid Pattern",
        "opening",
        placeholders="Musterfirma",
        text=(
            "Ich will bei Musterfirma arbeiten, weil mich das Thema lange "
            "begleitet und ich es konkret weiterbauen will. Diese Verbindung "
            "macht den Wechsel fuer mich plausibel."
        ),
        use_when="If the product is unusually compelling.",
        why_it_works="It is specific.",
    )

    if loader_name == "parse_library":
        with pytest.raises(CoverPatternError, match="unknown cover slot"):
            parse_library(text)
    else:
        path = tmp_path / "cover-patterns.md"
        path.write_text(text)
        with pytest.raises(CoverPatternError, match="unknown cover slot"):
            load_library(path)


def test_cover_pattern_library_load_rejects_empty_required_metadata_value(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cover-patterns.md"
    path.write_text(
        _pattern_block(
            "Missing Argument Type",
            "cover_intro",
            argument_type="",
            placeholders="Musterfirma",
            text=(
                "Ich will bei Musterfirma arbeiten, weil mich das Thema lange "
                "begleitet und ich es konkret weiterbauen will. Diese "
                "Verbindung macht den Wechsel fuer mich plausibel."
            ),
            use_when="If the product is unusually compelling.",
            why_it_works="It is specific.",
        )
    )

    with pytest.raises(
        CoverPatternError,
        match="Missing Argument Type: missing required metadata: argument_type",
    ):
        CoverPatternLibrary.load(path)


def test_cover_pattern_library_load_reports_each_missing_required_metadata_key(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cover-patterns.md"
    path.write_text(
        textwrap.dedent(
            """\
            ## Missing Metadata
            - slot: cover_intro
            - argument_type:
            - placeholders:

            Ich will bei Musterfirma arbeiten, weil mich das Thema lange begleitet und ich es konkret weiterbauen will. Diese Verbindung macht den Wechsel fuer mich plausibel.
            """
        )
    )

    with pytest.raises(
        CoverPatternError,
        match=(
            "Missing Metadata: missing required metadata: "
            "argument_type, placeholders, use_when, why_it_works"
        ),
    ):
        CoverPatternLibrary.load(path)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            _pattern_block(
                "Empty Text",
                "cover_intro",
                placeholders="Musterfirma",
                text="",
                use_when="If the product is unusually compelling.",
                why_it_works="It is specific.",
            ),
            "text paragraph is empty",
        ),
        (
            _pattern_block(
                "One Sentence",
                "cover_intro",
                placeholders="Musterfirma",
                text="Ich will bei Musterfirma arbeiten.",
                use_when="If the product is unusually compelling.",
                why_it_works="It is specific.",
            ),
            "must contain at least two sentences",
        ),
        (
            _pattern_block(
                "Undeclared Placeholder",
                "cover_intro",
                placeholders="Musterfirma",
                text=(
                    "Ich will bei Musterfirma arbeiten, weil Musterprodukt fuer "
                    "mich ein glaubwuerdiger Hebel ist und ich dazu bereits "
                    "belastbare Erfahrung gesammelt habe. Diese Verbindung "
                    "wuerde ich im Team gern weiter ausbauen."
                ),
                use_when="If the product is unusually compelling.",
                why_it_works="It is specific.",
            ),
            "undeclared placeholders in text",
        ),
        (
            _pattern_block(
                "Unsupported Placeholder",
                "cover_fit",
                argument_type="capability",
                placeholders="Musterfirma, Musterskill",
                text=(
                    "Bei Musterfirma kann ich Musterskill in einem Umfeld "
                    "einsetzen, in dem ich bereits belastbare Wirkung gezeigt "
                    "habe. Diese Verantwortung moechte ich bewusst weiter "
                    "tragen."
                ),
                use_when="If a role maps clearly to prior evidence.",
                why_it_works="It ties evidence to the role.",
            ),
            "unsupported placeholder",
        ),
        (
            textwrap.dedent(
                """\
                ## Missing Metadata
                - slot: cover_intro
                - use_when: If the product is unusually compelling.
                - placeholders: Musterfirma
                - why_it_works: It is specific.

                Ich will bei Musterfirma arbeiten, weil mich das Thema lange begleitet und ich es konkret weiterbauen will. Diese Verbindung macht den Wechsel fuer mich plausibel.
                """
            ),
            "missing required metadata",
        ),
        (
            _pattern_block(
                "Multi Paragraph",
                "cover_intro",
                placeholders="Musterfirma",
                text=(
                    "Ich will bei Musterfirma arbeiten, weil mich das Thema "
                    "lange begleitet und ich es konkret weiterbauen will.\n\n"
                    "Der zweite Absatz duerfte hier nicht erlaubt sein, weil "
                    "ein Muster genau einen Absatz enthalten muss."
                ),
                use_when="If the product is unusually compelling.",
                why_it_works="It is specific.",
            ),
            "must contain exactly one paragraph",
        ),
    ],
)
def test_cover_pattern_library_parse_rejects_invalid_patterns(
    text: str, message: str
) -> None:
    with pytest.raises(CoverPatternError, match=message):
        CoverPatternLibrary.parse(text)


@pytest.mark.parametrize("placeholder", ["Musterdomäne", "Mustertechnologie"])
def test_cover_pattern_library_parse_accepts_canonical_placeholder_vocabulary(
    placeholder: str,
) -> None:
    library = CoverPatternLibrary.parse(
        _pattern_block(
            "Vocab Pattern",
            "cover_intro",
            placeholders=f"Musterfirma, {placeholder}",
            use_when="When the domain maps clearly.",
            text=(
                f"Bei Musterfirma reizt mich besonders die Arbeit im Bereich "
                f"{placeholder}. Diese Verbindung habe ich bereits konkret "
                "erlebt und moechte sie weiterentwickeln."
            ),
        )
    )

    assert library.all_patterns()[0].placeholders == ("Musterfirma", placeholder)


def test_cover_pattern_library_parse_detects_umlaut_placeholder_as_undeclared() -> None:
    with pytest.raises(
        CoverPatternError,
        match="undeclared placeholders in text",
    ):
        CoverPatternLibrary.parse(
            _pattern_block(
                "Umlaut Undeclared",
                "cover_intro",
                placeholders="Musterfirma",
                use_when="When domain matches.",
                why_it_works="It is specific.",
                text=(
                    "Bei Musterfirma reizt mich die Arbeit in der Musterdomäne "
                    "besonders. Diese Verbindung habe ich bereits konkret "
                    "erlebt und moechte sie aktiv weiterentwickeln."
                ),
            )
        )


@pytest.mark.parametrize("seed", [None, "", "   \n"])
def test_load_tolerates_missing_or_empty_cover_patterns(
    seed: str | None, tmp_path: Path
) -> None:
    path = tmp_path / "cover-patterns.md"
    if seed is not None:
        path.write_text(seed)

    assert load(path) == []


def test_cover_pattern_library_loads_shipped_cover_patterns_template() -> None:
    library = CoverPatternLibrary.load(_COVER_PATTERNS_TEMPLATE)

    assert [pattern.name for pattern in library.all_patterns()] == [
        "Product Resonance Intro"
    ]
    assert [pattern.name for pattern in library.patterns_for_slot("cover_intro")] == [
        "Product Resonance Intro"
    ]


def test_cover_pattern_library_parse_accepts_markdown_sections_between_patterns() -> (
    None
):
    library = CoverPatternLibrary.parse(
        "\n\n".join(
            [
                "# Intro Patterns",
                _pattern_block("Product Resonance Intro", "cover_intro").strip(),
                "# Closing Patterns",
                _pattern_block(
                    "Product Resonance Closing",
                    "cover_closing",
                    argument_type="closing",
                    placeholders="Musterfirma, Musterrolle",
                    use_when=(
                        "Use when the close should stay direct and tie intent back "
                        "to the role."
                    ),
                    why_it_works=(
                        "It closes with clear intent and keeps the role reference "
                        "explicit."
                    ),
                    text=(
                        "Deshalb moechte ich meine Erfahrung bei Musterfirma in der "
                        "Musterrolle wirksam einbringen. Ueber ein Gespraech dazu, "
                        "wie ich den Beitrag konkret leisten kann, freue ich mich."
                    ),
                ).strip(),
            ]
        )
    )

    assert [pattern.name for pattern in library.all_patterns()] == [
        "Product Resonance Intro",
        "Product Resonance Closing",
    ]


def test_cover_pattern_library_parse_rejects_non_pattern_text_after_markdown_section_heading() -> (
    None
):
    text = "\n\n".join(
        [
            "# Intro Patterns",
            _pattern_block("Product Resonance Intro", "cover_intro").strip(),
            "# Closing Patterns",
            "This line is not a pattern header and must not be ignored.",
            _pattern_block(
                "Product Resonance Closing",
                "cover_closing",
                argument_type="closing",
                placeholders="Musterfirma, Musterrolle",
                use_when="Use when the close should stay direct and tie intent back to the role.",
                why_it_works="It closes with clear intent and keeps the role reference explicit.",
                text=(
                    "Deshalb moechte ich meine Erfahrung bei Musterfirma in der "
                    "Musterrolle wirksam einbringen. Ueber ein Gespraech dazu, wie "
                    "ich den Beitrag konkret leisten kann, freue ich mich."
                ),
            ).strip(),
        ]
    )

    with pytest.raises(
        CoverPatternError,
        match="content outside a cover pattern block is not supported",
    ):
        CoverPatternLibrary.parse(text)
