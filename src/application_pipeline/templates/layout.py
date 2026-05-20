# Each group collapses its fields with the separator, omitting None values.
# The result replaces the group name as a placeholder in the templates below.
PLACEHOLDER_GROUPS = {
    "title_line": (" · ", ["company", "title", "location_segment"]),
    "meta_line": (" · ", ["salary", "posted_date", "contract_type", "employment_type"]),
}

# Live placeholders substituted by the renderer:
#   {company}, {title}, {location}, {location_segment}, {source}, {url}
#   {salary}, {posted_date}, {contract_type}, {employment_type}, {work_model}, {deadline}
#   {raw_description}
#   {matched}, {missing} — comma-joined lists; "" when empty
#   {summary} — verdict AI assessment summary
#   {rank} — verdict rank (1–5)
#   Plus any keys defined in PLACEHOLDER_GROUPS above.
CARD_TEMPLATE = """\
# **{rank}:** {title_line}

{meta_line}

## AI Assessment

{summary}

## Job Description

{raw_description}

---
<{url}>
"""
