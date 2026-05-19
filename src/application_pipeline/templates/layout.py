# Each group collapses its fields with the separator, omitting None values.
# The result replaces the group name as a placeholder in the templates below.
PLACEHOLDER_GROUPS = {
    "meta_line": (" · ", ["posted_date", "contract_type", "employment_type"]),
}

# Live placeholders substituted by the renderer:
#   {company}, {title}, {location_segment} — position header fields
#   {posted_date}, {contract_type}, {employment_type} — meta line (grouped via PLACEHOLDER_GROUPS)
#   {salary} — optional salary field
#   {summary} — verdict AI assessment summary
#   {matched_bullets}, {missing_bullets} — bullet lists of matched/missing requirements
#   {raw_description} — full job description text
#   {rank} — verdict rank (1–5)
#   {url} — position URL
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

**Rank:** {rank}

---
<{url}>
"""
