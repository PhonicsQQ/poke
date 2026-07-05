"""
Export the trending results to a static JSON for the GitHub Pages frontend.

Runs after ingestion in the daily workflow. Embeds each card's recent
price history so the page needs exactly one fetch (no API server).

Usage: python export_site.py [--limit 20] [--history-days 14]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR / "pokeprices.db"
OUT_PATH = SCRIPT_DIR / "docs" / "data" / "trending.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--history-days", type=int, default=14)
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    cards = [dict(r) for r in conn.execute(
        """
        SELECT card_id, name, image_url,
               current_price, price_7d_ago, pct_change, trend_rank
        FROM trending
        ORDER BY trend_rank
        LIMIT ?
        """,
        (args.limit,),
    )]

    for card in cards:
        rows = conn.execute(
            """
            SELECT snapshot_date,
                   COALESCE(price_market_holofoil,
                            price_market_normal,
                            price_market_reverse_holo) AS price
            FROM price_snapshots
            WHERE card_id = ?
            ORDER BY snapshot_date DESC
            LIMIT ?
            """,
            (card["card_id"], args.history_days),
        ).fetchall()
        card["history"] = [dict(r) for r in reversed(rows)]

    latest = conn.execute(
        "SELECT MAX(snapshot_date) FROM price_snapshots"
    ).fetchone()[0]
    conn.close()

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "latest_snapshot": latest,
        "cards": cards,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"Wrote {len(cards)} cards -> {OUT_PATH}")


if __name__ == "__main__":
    main()
