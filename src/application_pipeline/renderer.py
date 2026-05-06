from typing import Any

from .layout.types import Layout
from .llm.types import MatchTier, MatchVerdict


def render(
    position: dict[str, Any],
    verdict: MatchVerdict,
    number: int,
    layout: Layout,
) -> str:
    tier = verdict.tier

    placeholders: dict[str, Any] = dict(position)

    placeholders["tier"] = tier.value
    placeholders["matched"] = ", ".join(verdict.matched)
    placeholders["missing"] = ", ".join(verdict.missing)
    placeholders["summary"] = verdict.summary
    placeholders["emoji"] = layout.tier_emoji[tier.value]
    placeholders["color"] = layout.tier_color[tier.value]
    placeholders["number"] = number

    for group_name, (separator, fields) in layout.placeholder_groups.items():
        parts = [
            str(placeholders[f]) for f in fields if placeholders.get(f) is not None
        ]
        placeholders[group_name] = separator.join(parts)

    template = (
        layout.card_template if tier == MatchTier.green else layout.headline_template
    )

    return template.format_map(placeholders)
