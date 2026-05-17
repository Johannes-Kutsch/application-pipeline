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
    "meta_line": (" · ", ["posted_date", "contract_type", "employment_type"]),
}

CARD_TEMPLATE = """\
# {company} · {title} · {location_segment}

{posted_date} · {contract_type} · {employment_type}

**Salary:** {salary}

## AI Assessment

{summary}

**Matched:**
- ...

**Missing:**
- ...

## Job Description

{raw_description}

---
<{url}>
"""
