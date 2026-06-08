---
name: write-cv
description: Generates a tailored cv.tex (CV Slot-Map) plus application-suffixed cover/resume/combined PDFs for a listing previously analysed by /analyse-listing, then stays in the same resident edit loop for follow-up cv.tex, analysis.md, and triage-profile feedback until the user signals done. Calls `application-pipeline compile-cv` and iteratively strips content until cover ≤ 1 page and resume ≤ 2 pages. Runs when the user types /write-cv.
---

Read and follow the authoritative workflow body:

`../../../application-pipeline/agent-skills/write-cv.md`
