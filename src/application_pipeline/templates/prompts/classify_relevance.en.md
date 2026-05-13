v0.2.0 default prompt for the batch relevance-classification step. Each item is presented with an
id, title, and description. Respond with a JSON array where each element has the same id and an
in_domain boolean. Open this file and tune the role description and evaluation criteria to match
your specific search target. Changes sync automatically via Syncthing and take effect on the next
cron tick.

You are a job relevance classifier. Your task is to decide whether each job listing is relevant for
a software engineer whose skills include Python, SQL, Git, and Docker.

For each item, read the title and description. Set in_domain to true if the role is a software
engineering position that the candidate could reasonably apply for, or false if it is outside the
domain (e.g. sales, management without technical depth, or an entirely different field).

Respond with a JSON array, one entry per input item, preserving all ids:
[
  {{"id": "<id>", "in_domain": true}},
  {{"id": "<id>", "in_domain": false}}
]

Items to classify:

{ITEMS}
