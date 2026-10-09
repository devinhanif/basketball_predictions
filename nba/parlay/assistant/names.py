"""Player-name resolution for chat arguments. Never guesses: ambiguity is an error."""

from __future__ import annotations

import difflib
from collections.abc import Mapping, Sequence

from nba.kalshi.aliases import load_reviewed_aliases
from nba.parse.availability import normalize_name


class PlayerResolutionError(ValueError):
    pass


class NameResolver:
    def __init__(
        self,
        table: Mapping[str, Sequence[int]] | None = None,
        display: Mapping[int, str] | None = None,
    ) -> None:
        self._table: dict[str, list[int]] = {k: list(v) for k, v in (table or {}).items()}
        self._display: dict[int, str] = dict(display or {})

    @classmethod
    def default(cls) -> NameResolver:
        """Reviewed alias table plus nba_api's bundled static player list (a local file)."""
        table: dict[str, list[int]] = {}
        display: dict[int, str] = {}
        try:
            from nba_api.stats.static import players

            for p in players.get_players():
                table.setdefault(normalize_name(str(p["full_name"])), []).append(int(p["id"]))
                display[int(p["id"])] = str(p["full_name"])
        except Exception:  # static list unavailable: aliases only
            pass
        for name, pid in load_reviewed_aliases().items():
            ids = table.setdefault(name, [])
            if pid not in ids:
                ids.append(pid)
            display.setdefault(pid, name.title())
        return cls(table, display)

    def display(self, pid: int) -> str:
        return self._display.get(pid, f"player {pid}")

    def resolve(self, raw: str | int, candidates: set[int] | None = None) -> int:
        """Name or numeric id -> player_id, restricted to ``candidates`` when given."""
        if isinstance(raw, bool):
            raise PlayerResolutionError("player must be a name or id")
        if isinstance(raw, int) or str(raw).strip().isdigit():
            pid = int(raw)
            if candidates is not None and pid not in candidates:
                raise PlayerResolutionError(f"player id {pid} has no forward prediction that day")
            return pid
        key = normalize_name(str(raw))
        ids = self._table.get(key, [])
        if not ids:
            near = difflib.get_close_matches(key, self._table.keys(), n=3, cutoff=0.7)
            hint = f" Did you mean: {near}?" if near else ""
            raise PlayerResolutionError(f"unknown player {raw!r}; not guessing.{hint}")
        if candidates is not None:
            ids = [i for i in ids if i in candidates]
            if not ids:
                raise PlayerResolutionError(
                    f"{raw!r} is a known player but has no forward prediction that day"
                )
        if len(set(ids)) > 1:
            raise PlayerResolutionError(f"{raw!r} is ambiguous (ids {sorted(set(ids))}); use an id")
        return ids[0]
