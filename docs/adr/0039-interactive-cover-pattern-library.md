# Interactive cover pattern library

`/write-cv` drafts cover paragraphs interactively against a global `cover-patterns.md` library. For each cover slot: reuse matching candidate-approved pattern verbatim or lightly adapt, then confirm. On rejection, propose alternatives. New patterns written to `cover-patterns.md` only when candidate enters main drafting loop and explicitly approves a new slot-purpose + argument-type combination.

Interactive flow applies only to the four cover prose slots, not `opening`/resume/skills. Cover-shortening loop: propose full shortened paragraph per cover slot, candidate chooses one, only `cv.tex` changes before rebuild. Post-build shortening does not update `cover-patterns.md`.

## Why

- Learning at the quality-decision point. Library grows from confirmed quality, not one-shot generation.
