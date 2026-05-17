from .layout.types import Layout
from .llm.types import MatchVerdict
from .parsers.types import Position


def _location_segment(location: str | None, work_model: str | None) -> str:
    suffix = {
        "hybrid": " (Hybrid)",
        "remote": " (Remote)",
    }.get(work_model or "", "")
    base = location if location is not None else "Unknown Location"
    return base + suffix


def render(
    position: Position,
    verdict: MatchVerdict,
    layout: Layout,
) -> str:
    stub = position.stub
    loc_seg = _location_segment(stub.location, position.work_model)

    parts = []

    # H1
    company_prefix = f"{stub.company} · " if stub.company is not None else ""
    parts.append(f"# {company_prefix}{stub.title} · {loc_seg}")
    parts.append("")

    # Meta line: posted_date · contract_type · employment_type
    meta_fields = [
        position.posted_date,
        position.contract_type,
        position.employment_type,
    ]
    meta_parts = [str(f) for f in meta_fields if f is not None]
    if meta_parts:
        parts.append(" · ".join(meta_parts))
        parts.append("")

    # Salary
    if position.salary is not None:
        parts.append(f"**Salary:** {position.salary}")
        parts.append("")

    # AI Assessment
    parts.append("## AI Assessment")
    parts.append("")
    parts.append(verdict.summary)
    parts.append("")

    # Matched
    if verdict.matched:
        parts.append("**Matched:**")
        for item in verdict.matched:
            parts.append(f"- {item}")
        parts.append("")

    # Missing
    if verdict.missing:
        parts.append("**Missing:**")
        for item in verdict.missing:
            parts.append(f"- {item}")
        parts.append("")

    # Job Description
    if position.raw_description:
        parts.append("## Job Description")
        parts.append("")
        parts.append(position.raw_description)
        parts.append("")

    # Footer
    parts.append("---")
    parts.append(f"<{stub.url}>")
    parts.append("")

    return "\n".join(parts)
