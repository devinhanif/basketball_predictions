# health_strip v1: keys added to /api/health for the system strip (app/api, 2026-11-17)

`GET /api/health` keeps every key it had (`status`, `mode`, `data_made_at`, `data_age_s`, `data_version`, `export`)
unchanged and adds three. Code in `system_extra.strip_fields` and `honesty_data.load_strip`.

| key | value |
|---|---|
| `newest_quote` | `{label: "newest quote in this export", by_book: {book: ISO time}, pinnacle: ISO or null, source}`: `max(prop_lines.snapshot_ts)` per book, stored times only. **Not** odds capture status: a book can be absent because the capture failed or because it posted nothing. Null on the fixture replay |
| `stale_marks` | `{source, by_model: {model: {freshness_reason: n}}, primary_fallback_stale}`: rows of `prop_grids` with `freshness = 'stale'` (nba/export/best_model.py:76-87; `writer_flag` is the writer's `stale_model` flag), and the manifest's `primary_sources.fallback_stale`. Null on the fixture replay |
| `strip_missing` | what the strip wants but the export lacks: always odds capture status per book, and a per-row stale flag for production rows; plus Pinnacle when no Pinnacle quote is stored, and any read failure |

What was searched for a stale flag: `prediction_json` of every model in the frozen export (no `stale` key in any row),
`bundle_*` and `freshness*` columns (only AVG rows carry `freshness = 'stale'`, reason `writer_flag`, 15,276 rows on
2026-11-16), and the manifest (`primary_sources.fallback_stale` 6,604).

Note from the frozen export of 2026-11-16: every book's newest `snapshot_ts` is the same instant (2026-11-17T00:45:47Z),
which suggests it is the capture run's time rather than a per-book quote time; the label says "newest quote in this
export" and nothing more.

Measured: 2,486 B, about 1-2 ms.
