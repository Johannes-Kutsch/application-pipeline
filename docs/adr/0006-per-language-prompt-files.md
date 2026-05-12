# Per-language prompts via file-naming convention

LLM prompts are authored as full per-language translations rather than a single template with a `{language}` slot. Files live at `prompts/{call_site}.{lang}.md` (e.g. `prompts/classify_relevance.de.md`, `prompts/judge_match.en.md`). The **OllamaExtractor** selects a file by the **Position**'s detected language, falling back to the English file when language is `"unknown"` or `"other"`. The `language` field is no longer a prompt-template slot — the file *is* the language.

## Why

- **Qwen 3 4B's German output quality is materially better when the entire prompt frame is in German.** A single English prompt that injects "this listing is in German; emit summary in German" produces summaries that drift back to English mid-sentence and use stilted German idiom when they don't drift. Authoring the prompt natively in the target language fixes both.
- **The `{language}` slot was tautological.** With per-language files, the file IS the language; injecting the language as a slot value duplicates information the loader already knows.
- **Translation cost is bounded.** Two languages × two call sites = 4 files. The applicant's pipeline targets German job markets (Bundesagentur, stellen.hamburg) plus English-language listings; neither language set grows organically. A third language in v1.1+ is one new file per call site — additive.
- **Simpler validation contract.** PRD #24's Prompt Loader validates each file against its slot inventory independently; per-language doubling fits the existing shape with the slot inventory shrinking by one (the dropped `language` slot).

## Considered alternatives

- **Single template with `{language}` slot (PRD #24 as originally shipped).** Rejected: lower output quality on German listings; the slot was tautological with the embedded **Triage Profile** prose already being language-specific.
- **Shared scaffold + language-specific body, concatenated at load.** Considered, deferred: cleaner duplication-elimination but adds load-time concatenation logic to PRD #24 for marginal gain. Revisit if prompt-tuning friction from the duplicated scaffold becomes painful.
- **Per-language directory (`prompts/de/classify_relevance.md`).** Rejected: same file count, more nesting, harder to glance-read in `git status`.
- **Selector flag + branched template inside one file.** Rejected: pushes branching into a Markdown file; the Prompt Loader would need a templating dialect richer than `str.format`.

## Consequences

- `Config` exposes `prompts_dir: pathlib.Path = Path("prompts")` (one field) instead of two per-call-site path fields. This is a breaking change to PRD #29's already-shipped Config Loader; the loader is refactored as part of PRD #20's implementation.
- The Prompt Loader's `Prompts` dataclass exposes per-language access:
  ```python
  Prompts.classify_relevance: dict[Literal["de", "en"], PromptTemplate]
  Prompts.judge_match:        dict[Literal["de", "en"], PromptTemplate]
  ```
- `CLASSIFY_RELEVANCE_SLOTS` shrinks from `{title, raw_description, language}` to `{title, raw_description}`. `JUDGE_MATCH_SLOTS` is unchanged (`{skills, raw_description}`).
- The Prompt Loader validates 4 files at startup, each against its call site's slot inventory; missing files raise `PromptError` naming the missing file.
- Language fallback (`"unknown"` / `"other"` / unsupported) is an internal contract of `OllamaExtractor`: select the English file. Documented in PRD #20.
- A future third language is one Config-level decision (does the user want it?) plus 2 new files; no schema change.
- Prompt-tuning iterations require parallel edits across the language pair. Mitigation: a manual eyeball pass over both files when one changes; a future scaffold-extracting refactor is deferred to v1.1+.
