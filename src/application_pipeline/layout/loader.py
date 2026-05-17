import dataclasses
import pathlib
from datetime import date

from application_pipeline.llm.types import MatchTier, MatchVerdict
from application_pipeline.parsers.types import Position, PositionStub
from application_pipeline.renderer import render
from application_pipeline.user_settings import load_user_module

from .types import Layout, LayoutError

_REQUIRED_FIELDS = (
    "TIER_EMOJI",
    "TIER_COLOR",
    "PLACEHOLDER_GROUPS",
    "CARD_TEMPLATE",
)

_TIERS = frozenset({"green", "amber", "red"})

# Fields that may appear in PLACEHOLDER_GROUPS.
# Excludes renderer-derived fields (emoji, color, tier, number),
# verdict aggregates (matched, missing, summary), and raw_description.
_GROUPABLE_FIELDS = frozenset(
    {
        # PositionStub fields
        "title",
        "company",
        "location",
        "url",
        "source",
        # Position fields (excluding raw_description)
        "salary",
        "contract_type",
        "employment_type",
        "work_model",
        "posted_date",
        "deadline",
        # Renderer-added list variants
        "matched_bullets",
        "missing_bullets",
    }
)

# Smoke-test fixtures — built once at import, reused on every load().
_DENSE_STUB = PositionStub(
    url="https://example.com/job/1",
    title="Senior Engineer",
    source="example",
    company="ACME Corp",
    location="Berlin",
    posted_date=date(2026, 1, 15),
)

_DENSE_POSITION = Position(
    stub=_DENSE_STUB,
    raw_description="Full job description here.",
    salary="€80 000",
    contract_type="permanent",
    employment_type="full-time",
    work_model="hybrid",
    posted_date=date(2026, 1, 15),
    deadline=date(2026, 3, 31),
)

_SPARSE_STUB = PositionStub(
    url="https://example.com/job/2",
    title="Engineer",
    source="example",
)

_SPARSE_POSITION = Position(
    stub=_SPARSE_STUB,
    raw_description="",
)

_SMOKE_VERDICT = MatchVerdict(
    tier=MatchTier.green,
    matched=["Python", "FastAPI"],
    missing=["Kubernetes"],
    summary="Good candidate.",
)


def load(path: pathlib.Path) -> Layout:
    module = load_user_module(path, LayoutError)
    resolved = path.resolve()

    for name in _REQUIRED_FIELDS:
        if not hasattr(module, name):
            raise LayoutError(
                f"{name!r} at {resolved}: Missing required field",
                field=name,
                resolved_path=resolved,
            )

    layout = Layout(
        tier_emoji=module.TIER_EMOJI,
        tier_color=module.TIER_COLOR,
        placeholder_groups=module.PLACEHOLDER_GROUPS,
        card_template=module.CARD_TEMPLATE,
        empty_list_placeholder=getattr(module, "EMPTY_LIST_PLACEHOLDER", "—"),
    )
    _validate(layout, resolved)
    _smoke_test(layout, resolved)
    return layout


def _validate(layout: Layout, resolved_path: pathlib.Path) -> None:
    missing_emoji = _TIERS - set(layout.tier_emoji)
    if missing_emoji:
        raise LayoutError(
            f"'TIER_EMOJI' at {resolved_path}: missing tiers: {', '.join(sorted(missing_emoji))}",
            field="TIER_EMOJI",
            resolved_path=resolved_path,
        )

    extra_emoji = set(layout.tier_emoji) - _TIERS
    if extra_emoji:
        raise LayoutError(
            f"'TIER_EMOJI' at {resolved_path}: unknown tiers: {', '.join(sorted(extra_emoji))}",
            field="TIER_EMOJI",
            resolved_path=resolved_path,
        )

    missing_color = _TIERS - set(layout.tier_color)
    if missing_color:
        raise LayoutError(
            f"'TIER_COLOR' at {resolved_path}: missing tiers: {', '.join(sorted(missing_color))}",
            field="TIER_COLOR",
            resolved_path=resolved_path,
        )

    extra_color = set(layout.tier_color) - _TIERS
    if extra_color:
        raise LayoutError(
            f"'TIER_COLOR' at {resolved_path}: unknown tiers: {', '.join(sorted(extra_color))}",
            field="TIER_COLOR",
            resolved_path=resolved_path,
        )

    for group_name, (_, fields) in layout.placeholder_groups.items():
        for field in fields:
            if field not in _GROUPABLE_FIELDS:
                raise LayoutError(
                    f"'PLACEHOLDER_GROUPS' at {resolved_path}: group {group_name!r} references non-groupable field: {field!r}",
                    field="PLACEHOLDER_GROUPS",
                    resolved_path=resolved_path,
                )


def _smoke_test(layout: Layout, resolved_path: pathlib.Path) -> None:
    fixtures = [
        ("dense", _DENSE_POSITION),
        ("sparse", _SPARSE_POSITION),
    ]
    for density, position in fixtures:
        for tier in MatchTier:
            verdict = dataclasses.replace(_SMOKE_VERDICT, tier=tier)
            try:
                render(position, verdict, 1, layout)
            except Exception as exc:
                raise LayoutError(
                    f"smoke-test failed for {density} × {tier.value} at {resolved_path}: {exc}",
                    resolved_path=resolved_path,
                ) from exc
