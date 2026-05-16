import dataclasses
from typing import Any

from .layout.types import Layout
from .llm.types import MatchTier, MatchVerdict
from .parsers.types import Position

_EXCLUDED_POSITION_FIELDS = frozenset({"stub", "raw_description"})


def render(
    position: Position,
    verdict: MatchVerdict,
    number: int,
    layout: Layout,
) -> str:
    tier = verdict.tier

    placeholders: dict[str, Any] = {}

    # Flatten Position fields (excluding stub and raw_description)
    for f in dataclasses.fields(position):
        if f.name in _EXCLUDED_POSITION_FIELDS:
            continue
        placeholders[f.name] = getattr(position, f.name)

    # Flatten PositionStub fields
    for f in dataclasses.fields(position.stub):
        placeholders[f.name] = getattr(position.stub, f.name)

    # Verdict and derived fields
    placeholders["tier"] = tier.value
    placeholders["summary"] = verdict.summary
    placeholders["emoji"] = layout.tier_emoji[tier.value]
    placeholders["color"] = layout.tier_color[tier.value]
    placeholders["number"] = number

    # List placeholders with empty fallback
    empty = layout.empty_list_placeholder
    placeholders["matched"] = ", ".join(verdict.matched) if verdict.matched else empty
    placeholders["missing"] = ", ".join(verdict.missing) if verdict.missing else empty
    placeholders["matched_bullets"] = (
        "\n".join(f"- {item}" for item in verdict.matched) if verdict.matched else empty
    )
    placeholders["missing_bullets"] = (
        "\n".join(f"- {item}" for item in verdict.missing) if verdict.missing else empty
    )

    # Placeholder groups — wrap URL values in autolink form
    for group_name, (separator, fields) in layout.placeholder_groups.items():
        parts = []
        for field in fields:
            val = placeholders.get(field)
            if val is None:
                continue
            s = str(val)
            if s.startswith("http://") or s.startswith("https://"):
                s = f"<{s}>"
            parts.append(s)
        placeholders[group_name] = separator.join(parts)

    template = (
        layout.card_template if tier == MatchTier.green else layout.headline_template
    )

    return template.format_map(placeholders)
