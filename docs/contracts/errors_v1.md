# errors v1: the error-code registry as served JSON (app/api, 2026-11-17)

`GET /api/errors`. Read-only. Code in `system_extra.error_registry`, route in `routes/system.py`.

Returns `app/error_codes.json` as it is on disk: `{version, note, codes: [{code, status, side, title, what, why,
todo}]}`, both sides (`api` codes carry their HTTP status; `web` codes have `status: null`). The web help page and the
API read the same file, so they cannot drift.

Not backed by the export store: it answers 200 even when the export is refused (the help page must work then; the
route-contract sweep exempts it for that reason).

Tests: the payload equals the file; every code the API source raises (`ApiError("...")`, `error_response("...")`)
plus FastAPI's own (`not_found`, `method_not_allowed`, `bad_request`, `internal_error`) is in the served list with
`side: api` and a status.

New codes added for the honesty routes (2026-11-17): `no_game_forecast` (404) and `no_parlay_check` (404).

Measured on the frozen export of 2026-11-16: 7,933 B, about 1-2 ms.
