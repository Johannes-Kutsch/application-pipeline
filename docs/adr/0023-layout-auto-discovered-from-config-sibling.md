# Layout auto-discovered from `config.py`'s sibling

> **Amends [ADR-0005](0005-layout-as-user-editable-python-module.md):** the original ADR left the `LAYOUT` config field implicit and `layout/__init__.default()` was used as the silent fallback when unset. This ADR flips the default to "look for `layout.py` next to `config.py`, error if missing", matching how `USER_INFO_DIR` already auto-resolves.

When `LAYOUT` is unset in `config.py`, the loader defaults the path to `"layout.py"` (resolved against `config.py`'s parent directory) and treats a missing file as a hard error. An explicit `LAYOUT = None` in `config.py` is a supported opt-out that selects the built-in `layout.default()` stub; `layout.default()` is otherwise reserved for tests and programmatic callers.

## Why

- **Footgun closure.** Today, a user with a perfectly good `synched/layout.py` who forgot to add `LAYOUT = "layout.py"` to `config.py` silently gets the minimal stub (`## {number}. {title}  {emoji}`) with no color, no meta, no body. The failure mode is "results render but look wrong" — the worst kind because it doesn't surface until the next time someone opens `current.md`.
- **Pattern symmetry with `USER_INFO_DIR`.** `config/loader.py` already defaults `USER_INFO_DIR` to `"user-info"` relative to `config.py`'s parent and errors if the directory is missing. `init` seeds both `layout.py` and `user-info/`; they have identical lifecycle and should have identical default-resolution behavior.
- **`init` guarantees the file exists in healthy deployments.** Per ADR-0013, `python -m application_pipeline init <dir>` writes `layout.py` from the package template. A missing `layout.py` in a deployment that ran `init` is itself a bug worth surfacing, not silently papering over.
- **Explicit opt-out preserves the escape hatch.** `LAYOUT = None` keeps `layout.default()` reachable for tests and minimal programmatic callers without making "I forgot" and "I meant to" look the same to the loader.

## Considered alternatives

- **Keep current behavior; document the footgun in the template `config.py`.** Rejected: documentation in a file the user may never re-read after `init` doesn't beat a loader that just does the right thing. The footgun already cost one debugging session.
- **Auto-discover but silently fall back to `default()` if the sibling is missing.** Rejected: shrinks the footgun without closing it — renaming `layout.py` by accident still degrades to the stub. The "explicit > implicit" principle ADR-0013 cited against auto-materialise applies here too: failing loud beats degrading silent.
- **Auto-materialise `layout.py` from the package template if missing.** Rejected by ADR-0013 already, for the same reason: loader doing silent I/O writes surprises laptop dev users with typo'd config paths. `init` is the only path that writes user-edited files.
- **Make `layout.default()` the production fallback formally** (drop the opt-out). Rejected: the stub is genuinely useful for tests and one-off programmatic uses; deleting that affordance to enforce "always have a real layout" doesn't pay for itself.

## Consequences

- **Default `LAYOUT` value flips** from "unset → `layout.default()`" to "unset → `config_dir / 'layout.py'` (required)".
- **`config/loader.py` change**: when the user module has no `LAYOUT` attribute, default to `config_dir / "layout.py"` and resolve via `_resolve_optional_file` with the must-exist check. When `LAYOUT` is explicitly `None`, skip resolution and let the orchestrator use `layout_module.default()`.
- **Template `config.py` gains a one-line discoverability comment** documenting both the default and the `LAYOUT = None` opt-out. Discoverability is cheap; the magic still does the right thing for users who never read the comment.
- **Existing deployments without a sibling `layout.py`** now error at startup instead of producing stub-rendered results. In practice this is no one yet — `init` has always seeded `layout.py`, and the only live deployment (this repo) already has one.
- **ADR-0005's example `PLACEHOLDER_GROUPS` line** mentioning `"language"` remains historical; the field was never added to `Position`/`PositionStub` and the package template has long since dropped it. No update needed to ADR-0005 beyond the amendment marker.
