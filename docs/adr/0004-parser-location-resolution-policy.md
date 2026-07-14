# Parser location resolution as per-parser predicate + transform

Orchestrator → parser seam carries typed `Location = City(name) | Remote`. Each **Parser** declares coverage: `serves(name)`, `to_wire(name)`, `serves_remote`, `remote_wire()`. **Config Loader** validates at load time — `ConfigError` with `difflib.get_close_matches` hint.

## Why

- Sealed type makes meaning structural. Retires per-parser `_LOCATION_SLUGS` dicts.

## Consequences

- `ParserQuery.location` is sealed `City(name) | Remote` in `parsers/types.py`.
- `parsers/location.py` owns sealed types, `resolve()`, load-time validation.
