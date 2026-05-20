# Parser location resolution as per-parser predicate + transform

The orchestrator → parser seam carries a typed `Location = City(name) | Remote` instead of `str | None`. Each **Parser** module declares its geographic coverage and wire-format translation locally via four module-level symbols: `serves(name: str) -> bool`, `to_wire(name: str) -> str`, `serves_remote: bool`, `remote_wire() -> Any`. A shared helper `parsers/location.py` exposes `resolve(location, parser_module) -> Resolved | NotServed | RemoteWire`. The **Config Loader** validates at load time that every `LOCATIONS` entry is served by at least one configured **Source**, and that `INCLUDE_REMOTE=True` is satisfied by at least one `serves_remote` source.

## Why

- **Sealed type makes the meaning structural.** `query.location is None` meant "remote-only call" only by prose convention. `case Remote()` reads itself and gets exhaustive matching.
- **The duplication being retired is the slug tables, not "location handling".** Each parser previously carried a 28–33-row `_LOCATION_SLUGS` dict, half umlaut-variant duplicates. Parsers declare a predicate + transform, no city listing. Nationwide parsers: `serves = lambda _: True`; geo-pinned: `serves = lambda n: normalize(n) == "hamburg"`.
- **Honest user-facing failure on typos.** Load-time validation rejects unresolvable entries with `ConfigError: no configured source serves location 'Atlantis'` and a `difflib.get_close_matches` hint.
- **Remote stays per-parser because the wire shape differs.** Bundesagentur uses `arbeitszeit=ho`; jobs-beim-staat uses `place=homeoffice`; stellen.hamburg has no remote facet. `remote_wire()` returns whatever the parser needs.
- **The helper is the seam, not the policy.** It owns the sealed types and `resolve()` dispatch — no city catalog, no normalization map.

## Considered alternatives

- **`Anywhere` variant alongside `City` and `Remote`.** Rejected: no config knob emits it; no parser would treat it differently from `Remote`. `assert_never` makes adding it later cheap.
- **Shared canonical city catalog.** Rejected: duplicate truth. Coverage is what each parser supports.
- **Coverage on `SourceEntry` in `Config`.** Rejected: wire-form translation is parser-specific knowledge.
- **ASCII-fold umlauts in the helper.** Rejected: honest failure on typo'd second spellings beats magic input rewriting.
- **`COVERAGE: dict[str, str]` per parser.** Rejected: re-introduces slug-table maintenance.

## Consequences

- `ParserQuery.location` is sealed `City(name) | Remote` (lives in `parsers/types.py`).
- New module `parsers/location.py` owns the sealed `Location`, the result type `Resolved | NotServed | RemoteWire`, `resolve()`, and the load-time validation hook.
- Each parser exports the four `LocationCoverage` symbols. `Any` on `remote_wire` is deliberate — wire shapes are heterogeneous.
- Each parser's `discover` opens with one `match` on `resolve(query.location, _module)`. `NotServed` → empty generator → INFO log line.
- Runtime "this source doesn't serve this city" is no longer an error — it's an expected outcome of Cartesian expansion.
