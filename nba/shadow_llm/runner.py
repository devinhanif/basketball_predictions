"""``run_game``: one game, one request, strict validation, pre-tip write.

Order of refusals (nothing is sent unless every one passes): payload as-of rule (t <= tip - 60),
input cap, key present, budget guard. After the response: strict schema parse (a violation is
final, never retried), then ``made_at = received_at`` must be <= tip - 30 min and < tip, then the
rows are appended to ``forward_predictions`` through the store's own refuse-after-tip guard.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from nba.daily.store import ForwardPrediction, LeakageError, append_predictions
from nba.db.connect import connect_with_retry, is_lock_error
from nba.shadow_llm.calls import guard, open_shadow, record_call
from nba.shadow_llm.config import ShadowConfig, load_config
from nba.shadow_llm.payload import (
    AsOfViolation,
    PayloadRefused,
    build_payload,
    iso,
    naive_utc,
    payload_sha256,
)
from nba.shadow_llm.provider import (
    KEY_ENV,
    P_MAX,
    P_MIN,
    REASON_MAX_CHARS,
    Provider,
    ProviderError,
    ProviderSkip,
    ProviderUnavailable,
    prompt_sha256,
    scrub,
)

log = logging.getLogger("nba.shadow_llm")

MODEL_NAME = "llm_shadow"
VERSION = "v1"


class SchemaReject(ValueError):
    """The answer is not exactly the schema; the whole answer is discarded."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _is_num(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(float(v))


#: optional top-level boolean in the answer: the model's own report that a search result tried to
#: instruct it (web prompt only). Any other top-level key is still a schema violation.
INJECTION_KEY = "injection_suspected"


def injection_suspected(raw_text: str) -> bool | None:
    """The model's ``injection_suspected`` report; None when absent, not a boolean or not JSON."""
    try:
        obj = json.loads(raw_text)
    except (ValueError, TypeError):
        return None
    val = obj.get(INJECTION_KEY) if isinstance(obj, dict) else None
    return val if isinstance(val, bool) else None


def parse_response(raw_text: str, legs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strict parse against the payload's legs: exactly one entry per leg, in order, with
    player_id, stat and line copied exactly. Raises :class:`SchemaReject` on anything else
    (markdown fences, extra keys or legs, a renamed player, p_over outside [0.01, 0.99] or not a
    finite number, a changed line, a reason over 200 characters...). The only optional key is the
    top-level boolean ``injection_suspected``."""
    try:
        obj = json.loads(raw_text)
    except (ValueError, TypeError):
        raise SchemaReject("not_json") from None
    if (
        not isinstance(obj, dict)
        or set(obj) - {INJECTION_KEY} != {"forecasts"}
        or not isinstance(obj["forecasts"], list)
    ):
        raise SchemaReject("top_level_shape")
    if INJECTION_KEY in obj and not isinstance(obj[INJECTION_KEY], bool):
        raise SchemaReject("injection_suspected_type")
    items = obj["forecasts"]
    if len(items) != len(legs):
        raise SchemaReject(f"leg_count {len(items)} != {len(legs)}")
    out: list[dict[str, Any]] = []
    for leg, item in zip(legs, items, strict=True):
        if not isinstance(item, dict) or set(item) != {
            "player_id",
            "stat",
            "line",
            "p_over",
            "reason",
        }:
            raise SchemaReject("item_keys")
        pid, stat, line, p, reason = (
            item[k] for k in ("player_id", "stat", "line", "p_over", "reason")
        )
        if isinstance(pid, bool) or not isinstance(pid, int) or not isinstance(stat, str):
            raise SchemaReject("item_types")
        if (pid, stat) != (int(leg["player_id"]), str(leg["stat"])):
            raise SchemaReject("leg_order_or_identity")
        if not _is_num(line) or abs(float(line) - float(leg["line"])) > 1e-9:
            raise SchemaReject("line_mismatch")
        if not _is_num(p) or not P_MIN <= float(p) <= P_MAX:
            raise SchemaReject("p_over_out_of_range")
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason.strip()) > REASON_MAX_CHARS
        ):
            raise SchemaReject("reason")
        out.append(
            {
                "player_id": pid,
                "stat": stat,
                "line": float(line),
                "p_over": float(p),
                "reason": reason.strip(),
            }
        )
    return out


def _rows(
    game_id: str,
    tip: datetime,
    payload: dict[str, Any],
    parsed: list[dict[str, Any]],
    *,
    model_id: str,
    p_sha: str,
    pl_sha: str,
    tokens_in: int,
    tokens_out: int,
    audit: dict[str, Any] | None = None,
) -> list[ForwardPrediction]:
    web: dict[str, Any] = {}
    if audit:  # so scoring can split on odds exposure without the shadow database
        web = {
            "provider": audit.get("provider"),
            "n_searches": audit.get("n_searches", 0),
            "odds_domain_flag": bool(audit.get("odds_domain_flag", False)),
            "odds_query_flag": bool(audit.get("odds_query_flag", False)),
            "injection_flag": bool(audit.get("injection_flag", False)),
            "injection_suspected": audit.get("injection_suspected"),
        }
    out: list[ForwardPrediction] = []
    for leg, item in zip(payload["legs"], parsed, strict=True):
        out.append(
            ForwardPrediction(
                game_id=game_id,
                tipoff=tip,
                model_name=MODEL_NAME,
                version=VERSION,
                target=str(leg["stat"]),  # the existing prop target naming: pts|reb|ast|fg3m
                player_id=int(leg["player_id"]),
                prediction={
                    "p_over": item["p_over"],
                    "line": item["line"],
                    "book": leg["book"],
                    "reason": item["reason"],
                    "prompt_sha256": p_sha,
                    "payload_sha256": pl_sha,
                    "model_id": model_id,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "tokens_scope": "call",
                    "p_over_prod": leg["p_over_prod"],
                    "pinnacle_line": leg.get("pinnacle_line"),
                    **web,
                },
            )
        )
    return out


def _result(status: str, **kw: Any) -> dict[str, Any]:
    return {"status": status, "rows_written": 0, **kw}


def run_game(
    game_id: str,
    *,
    provider: Provider,
    now: datetime,
    paths: Mapping[str, Path],
    cfg: ShadowConfig | None = None,
    dry_run: bool = False,
    max_season: int | None = None,
    clock: Callable[[], datetime] = utcnow,
    sleep: Callable[[float], None] = time.sleep,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Forecast one game. Returns ``{"status", "rows_written", ...}`` with status ``ok``,
    ``schema_violation``, ``post_tip_refused`` or a refusal/skip code (``budget_skip``,
    ``monthly_cap_usd_not_set``, ``monthly_backstop``, ``key_missing``, ``provider_error``,
    ``no_legs``, ``input_cap``, ``as_of_violation``, ...). ``paths`` keys: facts, forward, market,
    shadow, nba."""
    cfg = cfg or load_config()
    env = os.environ if environ is None else environ
    real = bool(getattr(provider, "is_real", True))
    now = naive_utc(now)
    try:
        payload = build_payload(game_id, now=now, paths=paths, cfg=cfg, max_season=max_season)
    except AsOfViolation as exc:
        return _result("as_of_violation", detail=str(exc))
    except PayloadRefused as exc:
        return _result(exc.code, detail=str(exc))
    tip = datetime.fromisoformat(payload["game"]["tip_utc"])
    slate = date.fromisoformat(payload["game"]["et_date"])
    pl_sha = payload_sha256(payload)
    p_sha = str(getattr(provider, "prompt_hash", "") or prompt_sha256())
    needs_key = bool(getattr(provider, "needs_api_key", True))

    shadow = open_shadow(Path(paths["shadow"]))
    try:

        def rec(status: str, req: datetime, recv: datetime | None, res: dict[str, Any] | None,
                detail: str = "", skip: bool = False,
                audit: dict[str, Any] | None = None) -> None:  # fmt: skip
            usage = (res or {}).get("usage", {})
            mid = str((res or {}).get("model_id", getattr(provider, "model_id", "")))
            record_call(
                shadow, game_id=game_id, slate=slate, requested_at=req, received_at=recv,
                status=status, model_id=mid, prompt_sha256=p_sha, payload_sha256=pl_sha,
                tokens_in=int(usage.get("tokens_in", 0)),
                tokens_out=int(usage.get("tokens_out", 0)),
                budget_skip=skip, provider_real=real, detail=detail,
                audit=audit if audit is not None else (res or {}).get("audit"),
            )  # fmt: skip

        if real and needs_key and not env.get(KEY_ENV):
            return _result("key_missing", detail=f"{KEY_ENV} is not set", payload_sha256=pl_sha)
        why = guard(
            shadow, slate, cfg, real=real,
            usd_cap_required=bool(getattr(provider, "uses_usd_cap", True)),
            usage_limit_stops_night=bool(getattr(provider, "guards_usage_limit", False)),
        )  # fmt: skip
        if why is not None:
            ts = clock()
            rec(why, ts, None, None, detail="refused before the call", skip=True)
            return _result(why, payload_sha256=pl_sha)

        res: dict[str, Any] | None = None
        requested = received = clock()
        for attempt in range(1 + max(0, cfg.transport_retries)):
            requested = clock()
            try:
                res = provider.forecast(payload)
                received = clock()
                break
            except ProviderUnavailable as exc:
                return _result(exc.status, detail=scrub(str(exc), env), payload_sha256=pl_sha)
            except ProviderSkip as skip:  # logged, never retried, never an API fallback
                detail = scrub(skip.detail, env)
                rec(skip.status, requested, clock(), None, detail=detail, audit=skip.audit)
                log.warning("%s %s: %s", skip.status, game_id, detail)
                return _result(skip.status, detail=detail, night_stop=skip.night_stop,
                               payload_sha256=pl_sha)  # fmt: skip
            except Exception as exc:  # any provider failure: key-scrubbed, retried only pre-tip-45
                err = (
                    exc
                    if isinstance(exc, ProviderError)
                    else ProviderError(scrub(f"{type(exc).__name__}: {exc}", env))
                )
                rec("provider_error", requested, clock(), None, detail=str(err))
                log.error("provider_error %s attempt %d: %s", game_id, attempt + 1, err)
                last = attempt + 1 >= 1 + max(0, cfg.transport_retries)
                if last or not clock() < tip - timedelta(minutes=cfg.retry_until_min):
                    return _result("provider_error", detail=str(err), payload_sha256=pl_sha)
                sleep(min(2.0 * 2**attempt, 10.0))
        assert res is not None
        if isinstance(res.get("audit"), dict):  # the model's own injection report, if it gave one
            res["audit"]["injection_suspected"] = injection_suspected(str(res["raw_text"]))
        usage = res["usage"]
        tin, tout = int(usage["tokens_in"]), int(usage["tokens_out"])
        try:
            parsed = parse_response(res["raw_text"], payload["legs"])
        except SchemaReject as rej:
            log.warning(
                "schema_reject %s: %s | %s", game_id, rej.reason, scrub(res["raw_text"], env)[:200]
            )
            rec("schema_violation", requested, received, res, detail=rej.reason)
            return _result("schema_violation", detail=rej.reason, tokens_in=tin, tokens_out=tout,
                           payload_sha256=pl_sha)  # fmt: skip
        if not (received <= tip - timedelta(minutes=cfg.min_lead_min) and received < tip):
            log.warning("post_tip_refused %s: made_at %s, tip %s", game_id, iso(received), iso(tip))
            rec("post_tip_refused", requested, received, res, detail=f"made_at {iso(received)}")
            return _result(
                "post_tip_refused", tokens_in=tin, tokens_out=tout, payload_sha256=pl_sha
            )
        if dry_run:
            rec("dry_run", requested, received, res, detail=f"{len(parsed)} legs")
            return _result("dry_run", legs=len(parsed), tokens_in=tin, tokens_out=tout,
                           payload_sha256=pl_sha)  # fmt: skip
        rows = _rows(game_id, tip, payload, parsed, model_id=str(res["model_id"]), p_sha=p_sha,
                     pl_sha=pl_sha, tokens_in=tin, tokens_out=tout,
                     audit=res.get("audit"))  # fmt: skip
        try:
            wcon = connect_with_retry(Path(paths["forward"]), max_wait_s=cfg.db_wait_s)
        except duckdb.IOException as exc:
            if not is_lock_error(exc):
                raise
            rec("db_busy_not_written", requested, received, res, detail="forecast made, not stored")
            return _result("db_busy_not_written", payload_sha256=pl_sha)
        try:
            have = wcon.execute(
                "SELECT count(*) FROM forward_predictions WHERE game_id = ? AND model_name = ?",
                [game_id, MODEL_NAME],
            ).fetchone()
            if have and have[0]:
                rec("already_written", requested, received, res)
                return _result("already_written", payload_sha256=pl_sha)
            if not clock() < tip:
                rec("post_tip_refused", requested, received, res, detail="write time >= tip")
                return _result("post_tip_refused", payload_sha256=pl_sha)
            try:
                n = append_predictions(
                    wcon, f"llm_shadow-{received:%Y%m%dT%H%M%S}-{game_id}", received, rows
                )
            except LeakageError as exc:
                rec("post_tip_refused", requested, received, res, detail=str(exc))
                return _result("post_tip_refused", detail=str(exc), payload_sha256=pl_sha)
        finally:
            wcon.close()
        rec("ok", requested, received, res, detail=f"{n} rows")
        return {"status": "ok", "rows_written": n, "tokens_in": tin, "tokens_out": tout,
                "payload_sha256": pl_sha}  # fmt: skip
    finally:
        shadow.close()
