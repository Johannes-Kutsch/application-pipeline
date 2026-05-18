"""Strip German boilerplate paragraphs from job listing text before judge prompting."""

_SENTINELS: list[str] = [
    "wir bieten",
    "was wir bieten",
    "über uns",
    "über das unternehmen",
    "unser angebot",
    "benefits",
    "das bieten wir",
    "wir als arbeitgeber",
    "das erwartet dich bei uns",
    "das erwartet sie bei uns",
    "bewerben sie sich",
    "bewerbung richten",
    "senden sie ihre bewerbung",
    "schicken sie ihre bewerbung",
    "ihre bewerbung",
    "bewerbungsschluss",
    "bewerbungsfrist",
    "wir freuen uns auf ihre bewerbung",
    "wir freuen uns auf deine bewerbung",
]


def strip_boilerplate(text: str) -> str:
    """Return text with the first sentinel-matched paragraph and everything after it removed."""
    paragraphs = text.split("\n\n")
    result: list[str] = []
    for paragraph in paragraphs:
        normalized = paragraph.strip().lower()
        if any(normalized.startswith(sentinel) for sentinel in _SENTINELS):
            break
        result.append(paragraph)
    return "\n\n".join(result).rstrip()
