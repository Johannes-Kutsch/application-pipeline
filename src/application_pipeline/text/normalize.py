def normalize(value: str | None) -> str | None:
    """Collapse whitespace and casefold; return None for empty/None input."""
    if value is None:
        return None
    collapsed = " ".join(value.split()).casefold()
    return collapsed or None
