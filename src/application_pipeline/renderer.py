from typing import Any

from .layout.types import Layout
from .llm.types import MatchTier, MatchVerdict
from .parsers.types import Position


def render(
    position: Position,
    verdict: MatchVerdict,
    number: int,
    layout: Layout,
) -> str:
    tier = verdict.tier

    placeholders: dict[str, Any] = {
        "url": position.stub.url,
        "title": position.stub.title,
        "source": position.stub.source,
        "company": position.stub.company,
        "location": position.stub.location,
        "language": position.stub.language,
        "raw_description": position.raw_description,
        "salary": position.salary,
        "contract_type": position.contract_type,
        "employment_type": position.employment_type,
        "work_model": position.work_model,
        "posted_date": position.posted_date,
        "deadline": position.deadline,
    }

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
