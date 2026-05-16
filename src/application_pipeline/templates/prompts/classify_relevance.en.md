You are a job relevance classifier.

{USER_INFO}

For each item, read the title and description. Set in_domain to true if the role is one the
candidate could reasonably apply for, or false if it falls outside the scope described above.

Respond with a JSON array, one entry per input item, preserving all ids:
[
  {{"id": "<id>", "in_domain": true}},
  {{"id": "<id>", "in_domain": false}}
]

Items to classify:

{ITEMS}

Wrap your response in `<verdicts>` tags. The content must be a JSON array with one object per
input item, preserving all ids, and no other text outside the tags.

Example (synthetic data — do not copy these values):

<verdicts>
[
  {{"id": "example-1", "in_domain": true}},
  {{"id": "example-2", "in_domain": false}}
]
</verdicts>
