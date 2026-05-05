# Match Tier judged directly by the LLM against a Triage Profile

The **Match Judge** assigns a **Match Tier** (`green | amber | red`) to each in-domain **Position** by passing the applicant's **Triage Profile** plus the **Position**'s `raw_description` to the local LLM and asking for a structured **Match Verdict** (`tier, matched, missing, summary`). There is no numeric **Match Score**, no `Skills ∩ Requirements` formula, and no separate `requirements` / `nice_to_have` extraction step.

## Why

- **String-intersection scoring is unreliable on real listings.** Skills appear as `"PyTorch"`, `"pytorch (latest)"`, `"deep learning frameworks"`, `"Python 3.10+"`. A naïve formula either undercounts (misses semantic matches) or over-couples to extraction normalisation, neither of which Qwen 2.5 7B does well.
- **A flat `skills` list cannot carry seniority or role-shape signal.** "Looking for IC, not management", "comfortable with junior roles outside main domain", "ML applied to games is a strong yes" — these are decisive triage signals that a hard-skill list cannot encode.
- **The bottleneck is human attention, not formula precision.** v1 exists to compress 100+ daily listings into a useful shortlist for one applicant. A holistic LLM judgment matches the actual decision the applicant would make if they read every listing — which is the standard the pipeline is replacing.
- **One LLM call replaces two stages.** The previous design ran extraction (`requirements`, `nice_to_have`) and then matching as separate steps. Folding judgment into one prompt removes a layer and removes the `requirements` field from the **Position Schema**.
- **Two-pass shape is preserved.** `classify_relevance` (cheap, in-domain bool) still runs first so off-domain listings are discarded before the more expensive `judge_match` call.

## Considered alternatives

- **Word-boundary lowercased Skill ↔ Requirement matching** — rejected: cheap and transparent, but blind to phrasing and to non-skill signals (seniority, role shape). Originally proposed; rejected mid-design after recognising the human-attention bottleneck argument.
- **Single-pass 4-bucket call (`{green, amber, red, irrelevant}`)** — rejected: pays full extraction-shaped cost on every off-domain listing (Pflege etc.), and small models do single-objective prompts more reliably than multi-objective ones.
- **LLM-canonicalised requirements + formula matcher** — rejected: still anchors the pipeline to a percentage that the human would override anyway; adds canonicalisation as a new failure mode.

## Consequences

- The `LLMExtractor` Protocol changes shape: `extract_requirements` is removed; `judge_match(profile, skills, raw_description) -> MatchVerdict` is added.
- The **Position Schema** loses `requirements` and `nice_to_have`. **Match Verdict** carries `matched` and `missing` lists for **Card** rendering.
- The **Triage Profile** is a new artifact, living in the markdown body of `search_config.md`. It is distinct from the Phase-2 **CV Profile** and must not be conflated with it.
- **Headlines** show **Match Tier** (`green`/`amber`/`red`), not a percentage. There is no `match: —` empty-requirements special case — the LLM always emits a tier.
- Tier quality depends entirely on Qwen 2.5 7B's judgment. If false-greens or false-reds become a problem in real use, the response is prompt tuning, not formula tuning. Manual eyeballing of the **Results File** is the regression check.
- A future move to a stronger model (Llama 3.1 8B, or Anthropic API if budget changes) is a one-prompt swap inside `OllamaExtractor` / a new `LLMExtractor` implementation.
