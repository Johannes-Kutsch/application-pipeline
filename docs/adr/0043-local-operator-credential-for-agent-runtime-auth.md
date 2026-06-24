# Local Operator Credential for Agent Runtime auth

`OPENCODE_GO_API_KEY` in `<settings-dir>/.env` only. Shell environment and home-directory `.env` ignored. Not broad process config; does not make service/model/tool policy operator-configurable. Missing → startup failure before parser work begins. `init` seeds empty placeholder; `init --refresh` preserves.

## Why

- Explicit, single-source credential binding. No ambient env leakage.
