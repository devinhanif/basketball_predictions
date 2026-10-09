"""Diff two manifests and flag structural/content regressions and possible leaks."""

from __future__ import annotations

from typing import Any


def _flag(kind: str, table: str, detail: str, column: str = "") -> dict[str, Any]:
    return {"kind": kind, "table": table, "column": column, "detail": detail}


def leak_candidates(m: dict[str, Any], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Columns whose NULL rate differs sharply between played and unplayed rows."""
    thr = float(cfg["thresholds"]["asymmetry"])
    known = set(cfg.get("known_asymmetry", []))
    out = []
    for tname, t in m["tables"].items():
        for c, info in t["columns"].items():
            a = info.get("asymmetry")
            if a is not None and a >= thr and f"{tname}.{c}" not in known:
                out.append(
                    _flag(
                        "POSSIBLE_LEAK",
                        tname,
                        f"NULL rate played={info['null_rate_played']} vs "
                        f"unplayed={info['null_rate_unplayed']} (asymmetry {a:.3f})",
                        c,
                    )
                )
    return out


def diff_manifests(old: dict[str, Any], new: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Return ``{"changes": [...], "flags": [...]}`` describing old -> new."""
    th = cfg["thresholds"]
    changes: list[str] = []
    flags: list[dict[str, Any]] = []
    ot, nt = old["tables"], new["tables"]
    for t in sorted(set(nt) - set(ot)):
        changes.append(f"table added: {t} ({nt[t]['row_count']} rows)")
    for t in sorted(set(ot) - set(nt)):
        if t.startswith("parquet:"):
            # parquet files are opt-in per snapshot (--parquet); absence = not scanned
            changes.append(f"{t}: not scanned in the new snapshot")
            continue
        changes.append(f"table removed: {t}")
        flags.append(_flag("TABLE_REMOVED", t, "table no longer present"))
    for t in sorted(set(ot) & set(nt)):
        o, n = ot[t], nt[t]
        oc, nc = o["columns"], n["columns"]
        for c in sorted(set(nc) - set(oc)):
            changes.append(f"{t}: column added {c} ({nc[c]['type']})")
        for c in sorted(set(oc) - set(nc)):
            changes.append(f"{t}: column removed {c}")
            flags.append(_flag("COLUMN_REMOVED", t, "column no longer present", c))
        for c in sorted(set(oc) & set(nc)):
            if oc[c]["type"] != nc[c]["type"]:
                d = f"{oc[c]['type']} -> {nc[c]['type']}"
                changes.append(f"{t}.{c}: type change {d}")
                flags.append(_flag("TYPE_CHANGE", t, d, c))
        dr = n["row_count"] - o["row_count"]
        if dr:
            changes.append(f"{t}: rows {o['row_count']} -> {n['row_count']} ({dr:+d})")
        if dr < 0 and -dr > th["row_drop_frac"] * max(o["row_count"], 1):
            flags.append(_flag("ROW_DROP", t, f"rows {o['row_count']} -> {n['row_count']}"))
        if (n.get("pk_duplicates") or 0) > (o.get("pk_duplicates") or 0):
            flags.append(
                _flag("PK_DUPLICATES", t, f"{o.get('pk_duplicates')} -> {n['pk_duplicates']}")
            )
        for s, ent in (n.get("seasons") or {}).items():
            oe = (o.get("seasons") or {}).get(s)
            if oe is None:
                changes.append(f"{t}: season {s} added ({ent['rows']} rows)")
                continue
            cov_o, cov_n = oe.get("coverage"), ent.get("coverage")
            if cov_o is not None and cov_n is not None and cov_o - cov_n > th["coverage_drop"]:
                flags.append(_flag("COVERAGE_DROP", t, f"season {s}: {cov_o} -> {cov_n}"))
            elif cov_o != cov_n:
                changes.append(f"{t}: season {s} coverage {cov_o} -> {cov_n}")
        for c in sorted(set(oc) & set(nc)):
            ro, rn = oc[c]["null_rate"], nc[c]["null_rate"]
            if ro is not None and rn is not None and abs(rn - ro) > th["null_shift"]:
                flags.append(_flag("NULL_SHIFT", t, f"overall null rate {ro} -> {rn}", c))
            so, sn = oc[c].get("null_rate_by_season", {}), nc[c].get("null_rate_by_season", {})
            for s in sorted(set(so) & set(sn)):
                if abs(sn[s] - so[s]) > th["null_shift_season"]:
                    flags.append(
                        _flag("NULL_SHIFT", t, f"season {s} null rate {so[s]} -> {sn[s]}", c)
                    )
    for f in leak_candidates(new, cfg):
        t, c = f["table"], f["column"]
        oa = ot.get(t, {}).get("columns", {}).get(c, {}).get("asymmetry")
        if oa is None or oa < float(th["asymmetry_old_max"]):
            f["detail"] += (
                "; NEW (was " + ("absent/unmeasured" if oa is None else f"{oa:.3f}") + ")"
            )
            flags.append(f)
    if old["data_version"] != new["data_version"]:
        changes.insert(0, f"data_version {old['data_version']} -> {new['data_version']}")
    return {"changes": changes, "flags": flags}


def render_diff_md(result: dict[str, Any], old: dict[str, Any], new: dict[str, Any]) -> str:
    """Markdown rendering of a ``diff_manifests`` result."""
    lines = [
        f"## Data diff {old['data_version']} -> {new['data_version']}",
        f"({old.get('created_at', '?')} -> {new.get('created_at', '?')})",
        "",
        "### Flags",
    ]
    lines += [
        f"- **{f['kind']}** `{f['table']}{'.' + f['column'] if f['column'] else ''}`: {f['detail']}"
        for f in result["flags"]
    ] or ["- none"]
    lines += ["", "### Changes"]
    lines += [f"- {c}" for c in result["changes"]] or ["- none"]
    for tname in sorted(set(old.get("dir_caches", {})) | set(new.get("dir_caches", {}))):
        o = old.get("dir_caches", {}).get(tname, {"files": 0, "bytes": 0})
        n = new.get("dir_caches", {}).get(tname, {"files": 0, "bytes": 0})
        if o != n:
            lines.append(
                f"- cache data/{tname}: files {o['files']} -> {n['files']}, "
                f"bytes {o['bytes']} -> {n['bytes']}"
            )
    return "\n".join(lines)
