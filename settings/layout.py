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
    "meta": (" · ", ["location", "language", "url"]),
}

FILE_HEADER = "# Job Pipeline Results\n\n"

HEADLINE_TEMPLATE = "## {number}. {company} — {title}  {emoji}\n{meta}\n\n"

CARD_TEMPLATE = """\
## {number}. {company} — {title}  {emoji}
{meta}

**Matched:** {matched}
**Missing:** {missing}

{summary}

"""
