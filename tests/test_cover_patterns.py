import textwrap
from pathlib import Path

import pytest

from application_pipeline.cover_patterns import CoverPatternError, load, parse


def test_parse_returns_named_cover_paragraph_patterns() -> None:
    text = textwrap.dedent(
        """\
        ## Product Resonance Intro
        - slot: cover_intro
        - argument_type: resonance
        - use_when: The listing's product surface matches a long-running motivation.
        - placeholders: Musterfirma, Musterprodukt, Musterprojekt
        - why_it_works: It ties employer context to concrete candidate evidence.

        Bei Musterfirma reizt mich besonders, dass Musterprodukt ein Problem adressiert, das ich in Musterprojekt bereits aus der Builder-Perspektive durchdrungen habe. Gerade diese Naehe zwischen Produktproblem und Umsetzungserfahrung macht den Wechsel fuer mich plausibel.
        """
    )

    result = parse(text)

    assert [pattern.name for pattern in result] == ["Product Resonance Intro"]
    pattern = result[0]
    assert pattern.slot == "cover_intro"
    assert pattern.argument_type == "resonance"
    assert (
        pattern.use_when
        == "The listing's product surface matches a long-running motivation."
    )
    assert pattern.placeholders == (
        "Musterfirma",
        "Musterprodukt",
        "Musterprojekt",
    )
    assert (
        pattern.why_it_works
        == "It ties employer context to concrete candidate evidence."
    )
    assert (
        pattern.text
        == "Bei Musterfirma reizt mich besonders, dass Musterprodukt ein Problem adressiert, das ich in Musterprojekt bereits aus der Builder-Perspektive durchdrungen habe. Gerade diese Naehe zwischen Produktproblem und Umsetzungserfahrung macht den Wechsel fuer mich plausibel."
    )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            """\
            ## Unknown Slot
            - slot: opening
            - argument_type: resonance
            - use_when: If the product is unusually compelling.
            - placeholders: Musterfirma
            - why_it_works: It is specific.

            Ich will bei Musterfirma arbeiten, weil mich das Thema lange begleitet und ich es konkret weiterbauen will.
            """,
            "unknown cover slot",
        ),
        (
            """\
            ## Missing Metadata
            - slot: cover_intro
            - use_when: If the product is unusually compelling.
            - placeholders: Musterfirma
            - why_it_works: It is specific.

            Ich will bei Musterfirma arbeiten, weil mich das Thema lange begleitet und ich es konkret weiterbauen will.
            """,
            "missing required metadata",
        ),
        (
            """\
            ## Empty Text
            - slot: cover_intro
            - argument_type: resonance
            - use_when: If the product is unusually compelling.
            - placeholders: Musterfirma
            - why_it_works: It is specific.
            """,
            "text paragraph is empty",
        ),
        (
            """\
            ## Multi Paragraph
            - slot: cover_intro
            - argument_type: resonance
            - use_when: If the product is unusually compelling.
            - placeholders: Musterfirma
            - why_it_works: It is specific.

            Ich will bei Musterfirma arbeiten, weil mich das Thema lange begleitet und ich es konkret weiterbauen will.

            Der zweite Absatz duerfte hier nicht erlaubt sein, weil ein Muster genau einen Absatz enthalten muss.
            """,
            "must contain exactly one paragraph",
        ),
        (
            """\
            ## One Sentence
            - slot: cover_intro
            - argument_type: resonance
            - use_when: If the product is unusually compelling.
            - placeholders: Musterfirma
            - why_it_works: It is specific.

            Ich will bei Musterfirma arbeiten.
            """,
            "must contain at least two sentences",
        ),
        (
            """\
            ## Undeclared Placeholder
            - slot: cover_intro
            - argument_type: resonance
            - use_when: If the product is unusually compelling.
            - placeholders: Musterfirma
            - why_it_works: It is specific.

            Ich will bei Musterfirma arbeiten, weil Musterprodukt fuer mich ein glaubwuerdiger Hebel ist und ich dazu bereits belastbare Erfahrung gesammelt habe. Diese Verbindung wuerde ich im Team gern weiter ausbauen.
            """,
            "undeclared placeholders in text",
        ),
        (
            """\
            ## Unsupported Placeholder
            - slot: cover_fit
            - argument_type: capability
            - use_when: If a role maps clearly to prior evidence.
            - placeholders: Musterfirma, Musterskill
            - why_it_works: It ties evidence to the role.

            Bei Musterfirma kann ich Musterskill in einem Umfeld einsetzen, in dem ich bereits belastbare Wirkung gezeigt habe und die Verantwortung bewusst tragen will.
            """,
            "unsupported placeholder",
        ),
    ],
)
def test_parse_rejects_invalid_cover_patterns(text: str, message: str) -> None:
    with pytest.raises(CoverPatternError, match=message):
        parse(textwrap.dedent(text))


@pytest.mark.parametrize("seed", [None, "", "   \n"])
def test_load_tolerates_missing_or_empty_cover_patterns(
    seed: str | None, tmp_path: Path
) -> None:
    path = tmp_path / "cover-patterns.md"
    if seed is not None:
        path.write_text(seed)

    assert load(path) == []
