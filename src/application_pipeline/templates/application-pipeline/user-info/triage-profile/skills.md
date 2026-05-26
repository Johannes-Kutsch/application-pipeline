<!-- Skill pool for the pipeline judge and /write-cv.
     Grammar: H2 = group name; {always} = group always renders;
     {mle=high, games=low} = per-jobtype relevance (group is LLM-picked by listing).
     Item-level {always} is a within-group floor — it does NOT promote its group.
     Bare bullets (no {...}) are "LLM may pick" defaults inside an included group.
     Full grammar: docs/adr/0033-skills-grouped-dual-consumed-pool.md -->

## Core {always}

- Python
- SQL
- Git

## Machine Learning {mle=high, games=low, agents=medium}

- Pandas {always}
- scikit-learn
- PyTorch

## Game Development {games=high, mle=low}

- Unity
- C#
