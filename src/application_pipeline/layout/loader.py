import pathlib
from datetime import date

from application_pipeline.llm.types import MatchVerdict
from application_pipeline.parsers.types import Position, PositionStub
from application_pipeline.renderer import render
from application_pipeline.user_settings import load_user_module

from .types import Layout, LayoutError

_REQUIRED_FIELDS = (
    "PLACEHOLDER_GROUPS",
    "CARD_TEMPLATE",
)

# Module-level variable names from the pre-ADR-0029 layout format that are no longer supported.
_RETIRED_MODULE_VARS: dict[str, str] = {
    "TIER_EMOJI": "tier_emoji",
    "TIER_COLOR": "tier_color",
}

# Placeholders in CARD_TEMPLATE that were retired with ADR-0029.
_RETIRED_PLACEHOLDERS = ("emoji", "color", "tier")

# Fields that may appear in PLACEHOLDER_GROUPS.
# Excludes renderer-derived fields (emoji, color, tier, number),
# verdict aggregates (matched, missing, summary), and raw_description.
# Exception: location_segment is renderer-derived but declared groupable by ADR-0004.
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
        # Renderer-derived groupable field (ADR-0004)
        "location_segment",
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
    matched=["Python", "FastAPI"],
    missing=["Kubernetes"],
    summary="Good candidate.",
)


def load(path: pathlib.Path) -> Layout:
    module = load_user_module(path, LayoutError)
    resolved = path.resolve()

    for var_name, keyword in _RETIRED_MODULE_VARS.items():
        if hasattr(module, var_name):
            raise LayoutError(
                f"'{keyword}' is a retired layout keyword.",
                field=var_name,
                resolved_path=resolved,
            )

    for name in _REQUIRED_FIELDS:
        if not hasattr(module, name):
            raise LayoutError(
                f"{name!r} at {resolved}: Missing required field",
                field=name,
                resolved_path=resolved,
            )

    card_template: str = module.CARD_TEMPLATE
    for placeholder in _RETIRED_PLACEHOLDERS:
        if f"{{{placeholder}}}" in card_template:
            raise LayoutError(
                f"'{placeholder}' is a retired CARD_TEMPLATE placeholder.",
                field="CARD_TEMPLATE",
                resolved_path=resolved,
            )

    layout = Layout(
        placeholder_groups=module.PLACEHOLDER_GROUPS,
        card_template=card_template,
    )
    _validate(layout, resolved)
    _smoke_test(layout, resolved)
    return layout


def _validate(layout: Layout, resolved_path: pathlib.Path) -> None:
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
        try:
            render(position, _SMOKE_VERDICT, layout)
        except Exception as exc:
            raise LayoutError(
                f"smoke-test failed for {density} at {resolved_path}: {exc}",
                resolved_path=resolved_path,
            ) from exc
