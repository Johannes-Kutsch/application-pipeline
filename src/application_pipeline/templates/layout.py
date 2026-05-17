TIER_EMOJI = {
    "green": "🟢",
    "amber": "🟡",
    "red": "🔴",
}

TIER_COLOR = {
    "green": "#2ea043",
    "amber": "#d29922",
    "red": "#da3633",
}

# Each group collapses its fields with the separator, omitting None values.
# The result replaces the group name as a placeholder in the templates below.
PLACEHOLDER_GROUPS = {
    "meta": (" · ", ["location", "url"]),
}

EMPTY_LIST_PLACEHOLDER = "—"

FILE_HEADER = """\
# Job Pipeline Results
<!-- schema-version: 1 -->
<!-- Delete this file and re-run the pipeline to reset -->

"""

CARD_TEMPLATE = """\
## <span style="color:{color}">{number}. {company} — {title}  {emoji}</span>
{meta}

**Matched:** {matched}
**Missing:** {missing}

{summary}

"""
