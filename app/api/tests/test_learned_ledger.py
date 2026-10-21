"""Every number on the What-we-learned pane must appear in the ledger row or report it cites."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LEARNED = ROOT / "app" / "web" / "src" / "learned.json"
LEDGER = (ROOT / "docs" / "TEST_LEDGER.md").read_text()


def ledger_text(ids: list[str]) -> str:
    """Ledger rows whose first cell is a cited id, plus verdict paragraphs that mention one."""
    out: list[str] = []
    for line in LEDGER.splitlines():
        m = re.match(r"\|\s*(T\d+)", line)
        if m and m.group(1) in ids:
            out.append(line)
        elif any(i in line for i in ids) and line.startswith("**"):
            out.append(line)
    return "\n".join(out)


def test_every_token_is_in_its_source() -> None:
    items = json.loads(LEARNED.read_text())["items"]
    assert len(items) >= 9
    for it in items:
        sources = {e["n_source"] for e in it["evidence"]}
        text = ledger_text(it["ledger_ids"])
        for s in sources - {"ledger"}:
            text += "\n" + (ROOT / s).read_text()
        for tok in it["tokens"]:
            assert tok in text, f"{it['id']}: {tok!r} not found in its source"
        for e in it["evidence"]:
            for key in ("point", "lo", "hi"):
                v = e[key]
                if v is not None:
                    assert v in text, f"{it['id']}: {v!r} ({key}) not found"
            n = e["n"].split(" ")[0]
            assert n in text or n.replace(",", "") in text, f"{it['id']}: n {n!r} not found"


def test_every_ledger_id_exists() -> None:
    for it in json.loads(LEARNED.read_text())["items"]:
        for i in it["ledger_ids"]:
            assert re.search(rf"^\|\s*{i}\b", LEDGER, re.M), i
