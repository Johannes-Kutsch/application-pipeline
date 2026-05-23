# Hardcoded prompts with externalized user-info; single-language pipeline

> **Superseded by [ADR-0043](./0043-classifier-three-check-matches-verdict-and-triage-profile-merge.md).** The single `{USER_INFO}` slot and the per-call-site routing (classifier reads `self-description.md` + `domain-fit.md`; judge reads `self-description.md` + `match-criteria.md`) both retire. The current shape is three named slots — `{SELF_DESCRIPTION}`, `{MATCH_CRITERIA}`, `{SKILLS}` — placed by each prompt template under its own heading; `domain-fit.md` is folded into `match-criteria.md`. The hardcoded-package-prompts vs externalized-user-content split, the `<user-info>` framing principle, and the single-language (German) decision all survive. Read this ADR for the original separation rationale; read 0043 for the current slot model.

Prompts split into two parts. The **agent protocol** — task framing, output-tag instruction, JSON shape, synthetic example — is **hardcoded** inside the package at `src/application_pipeline/templates/prompts/`. The **applicant-specific content** — candidate self-description, in/out-of-domain rules, hard match criteria — lives in `data/user-info/` as three markdown files injected via a single `{USER_INFO}` slot wrapped in `<user-info>` tags. The **Prompt Loader** concatenates per call site:

- **Relevance Classifier**: `self-description.md` + `domain-fit.md`.
- **Match Judge**: `self-description.md` + `match-criteria.md`, plus the existing `{skills}` slot fed from `Config.SKILLS`.

In the same change, the pipeline drops per-language prompts and all language threading: one prompt per call site in German, regardless of listing language. `langdetect`, `LanguageResolution`, `resolve_language`, the `language` field on **Position Stub**, the `language` argument on `LLMExtractor` methods, and the per-language buffer split are removed.

## Why

- **Drift caused a silent production failure.** A single editable prompt file lost its `<verdicts>`-tag instruction during user edits, producing `tag_missing` on every classify call. Separating protocol (hardcoded) from content (user-editable) makes this class of drift structurally impossible.
- **The protocol is generic; the content isn't.** Output-tag contract, JSON shape, wrapping convention are properties of the **Agent Output Protocol** (ADR-0015). The applicant's self-description and domain rules are personal.
- **Three files, three concerns.** `self-description.md` is shared. `domain-fit.md` is classify-only. `match-criteria.md` is judge-only. Splitting along consumer boundaries means each call site gets only what it needs.
- **`SKILLS` stays in `config.py`.** Consumed by the judge's `{skills}` slot. A flat list of strings; extracting to `skills.md` would force markdown→list parsing.
- **Per-language separation was costing more than it bought.** Claude is bilingual at parity for these tasks; threading `language` everywhere was paying real maintenance cost. Applicant traffic is German-dominant; English listings remain classifiable by a German-framed prompt.

## Considered alternatives

- **Keep one editable prompt file per call site (fix the drift).** Rejected: re-creates the conditions for the next incident.
- **Startup validation that detects drift.** Rejected as standalone: solves the symptom; structural split solves the cause.
- **Concatenate user-info as a leading prose block.** Rejected: tag wrapping is cheap and makes the boundary explicit in transcripts.
- **Per-call-site user-info files.** Rejected: forces self-description duplication.
- **Single shared user-info file.** Rejected: sends classify the judge's match-criteria and vice versa.

## Consequences

- `Config` gains `USER_INFO_DIR: pathlib.Path` (default `user-info/`); `PROMPTS_DIR` is removed (prompts are package-internal).
- Package ships templates at `src/application_pipeline/templates/user-info/`, materialised into `data/user-info/` by `init` (ADR-0011).
- `Prompts` dataclass collapses from `dict[Literal["de","en"], PromptTemplate]` to one `PromptTemplate` per call site.
- The **Prompt Loader** reads hardcoded prompts via `importlib.resources`, reads user-info from `Config.USER_INFO_DIR`, concatenates, and renders `{USER_INFO}` with `<user-info>...</user-info>` wrapping. Missing/empty user-info files raise `PromptError` at startup.
- `LLMExtractor.classify_relevance_batch` and judge lose their `language` parameter; transcripts no longer carry `language`.
- `langdetect` is removed from `pyproject.toml`; `src/application_pipeline/language.py` is deleted.
- Hardcoded prompts instruct Claude to "respond in German regardless of input language" so the **Match Verdict**'s `summary` stays consistent for the **Renderer**.
- Re-introducing language separation later is mechanical and reversible by reverting this ADR's commit set.
