# Parser location resolution as per-parser predicate + transform

Orchestrator → parser seam carries typed `Location = City(name) | Remote` instead of `str | None`. Each **Parser** declares coverage via four module-level symbols: `serves(name) -> bool`, `to_wire(name) -> str`, `serves_remote: bool`, `remote_wire() -> Any`. **Config Loader** validates at load time that every `LOCATIONS` entry is served by at least one configured **Source**.

## Why

- Sealed type makes meaning structural — `case Remote()` reads itself.
- Retires 28–33-row `_LOCATION_SLUGS` dicts per parser. Parsers declare predicate + transform, no city listing.
- Load-time validation rejects unresolvable entries with `ConfigError` and `difflib.get_close_matches` hint.
- `remote_wire()` stays per-parser because wire shape differs across sources.

## Consequences

- `ParserQuery.location` is sealed `City(name) | Remote` in `parsers/types.py`.
- New module `parsers/location.py` owns the sealed types, `resolve()`, and load-time validation.
- Runtime "this source doesn't serve this city" is an expected outcome of Cartesian expansion, not an error.
