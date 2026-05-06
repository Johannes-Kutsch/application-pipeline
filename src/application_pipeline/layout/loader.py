import pathlib

from application_pipeline.user_settings import load_user_module

from .types import Layout, LayoutError

_REQUIRED_FIELDS = (
    "TIER_EMOJI",
    "TIER_COLOR",
    "PLACEHOLDER_GROUPS",
    "FILE_HEADER",
    "CARD_TEMPLATE",
    "HEADLINE_TEMPLATE",
)

_TIERS = frozenset({"green", "amber", "red"})

# All field names the Renderer may put into the placeholder dict.
_KNOWN_FIELDS = frozenset(
    {
        # Position schema
        "title",
        "company",
        "location",
        "language",
        "url",
        "source",
        "raw_description",
        "salary",
        "contract_type",
        "employment_type",
        "work_model",
        "posted_date",
        "deadline",
        # Match Verdict
        "tier",
        "matched",
        "missing",
        "summary",
        # Derived by Renderer
        "emoji",
        "color",
        "number",
    }
)


def load(path: pathlib.Path) -> Layout:
    module = load_user_module(path, LayoutError)

    for name in _REQUIRED_FIELDS:
        if not hasattr(module, name):
            raise LayoutError(f"Missing required field: {name}")

    layout = Layout(
        tier_emoji=module.TIER_EMOJI,
        tier_color=module.TIER_COLOR,
        placeholder_groups=module.PLACEHOLDER_GROUPS,
        file_header=module.FILE_HEADER,
        card_template=module.CARD_TEMPLATE,
        headline_template=module.HEADLINE_TEMPLATE,
    )
    _validate(layout)
    return layout


def _validate(layout: Layout) -> None:
    missing_emoji = _TIERS - set(layout.tier_emoji)
    if missing_emoji:
        raise LayoutError(
            f"TIER_EMOJI missing tiers: {', '.join(sorted(missing_emoji))}"
        )

    missing_color = _TIERS - set(layout.tier_color)
    if missing_color:
        raise LayoutError(
            f"TIER_COLOR missing tiers: {', '.join(sorted(missing_color))}"
        )

    for group_name, (_, fields) in layout.placeholder_groups.items():
        for field in fields:
            if field not in _KNOWN_FIELDS:
                raise LayoutError(
                    f"PLACEHOLDER_GROUPS[{group_name!r}] references unknown field: {field!r}"
                )
