from .layout.types import Layout
from .llm.types import MatchVerdict
from .parsers.types import Position


def _location_segment(location: str | None, work_model: str | None) -> str | None:
    if work_model == "hybrid":
        return f"{location} (Hybrid)" if location is not None else "(Hybrid)"
    if work_model == "remote":
        return f"{location} (Remote)" if location is not None else "(Remote)"
    return location  # None when location is None (on-site / unset)


def _build_placeholders(
    position: Position,
    verdict: MatchVerdict,
    layout: Layout,
) -> dict[str, str]:
    stub = position.stub

    raw: dict[str, str | None] = {
        "company": stub.company,
        "title": stub.title,
        "location": stub.location,
        "location_segment": _location_segment(stub.location, position.work_model),
        "source": stub.source,
        "url": stub.url,
        "salary": position.salary,
        "posted_date": str(position.posted_date)
        if position.posted_date is not None
        else None,
        "contract_type": position.contract_type,
        "employment_type": position.employment_type,
        "work_model": position.work_model,
        "deadline": str(position.deadline) if position.deadline is not None else None,
        "raw_description": position.raw_description,
        "matched": ", ".join(verdict.matched) if verdict.matched else None,
        "missing": ", ".join(verdict.missing) if verdict.missing else None,
        "summary": verdict.summary,
        "rank": str(verdict.rank),
    }

    # Direct placeholders: None → ""
    out: dict[str, str] = {k: v if v is not None else "" for k, v in raw.items()}

    # Group placeholders: None entries dropped; all-None collapses to ""
    for group_name, (separator, fields) in layout.placeholder_groups.items():
        parts = [v for f in fields if (v := raw.get(f)) is not None]
        out[group_name] = separator.join(parts) if parts else ""

    return out


def render(
    position: Position,
    verdict: MatchVerdict,
    layout: Layout,
) -> str:
    return layout.card_template.format_map(
        _build_placeholders(position, verdict, layout)
    )
