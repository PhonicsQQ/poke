"""
Export every card in the set to a static JSON for the GitHub Pages frontend.

For each card: metadata (name, rarity, number, set), current price,
price ~7 days ago, percent change, and recent price history for charts.
Cards without market prices are included (prices null) so the set is complete.

Usage: python export_site.py [--history-days 30]
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

CARDS_QUERY = """
WITH latest AS (
    SELECT MAX(snapshot_date) AS d FROM price_snapshots
),
baseline AS (
    SELECT MIN(snapshot_date) AS d
    FROM price_snapshots
    WHERE snapshot_date >= date((SELECT d FROM latest), '-7 day')
),
now_p AS (
    SELECT card_id,
           COALESCE(price_market_holofoil,
                    price_market_normal,
                    price_market_reverse_holo) AS price
    FROM price_snapshots
    WHERE snapshot_date = (SELECT d FROM latest)
),
then_p AS (
    SELECT card_id,
           COALESCE(price_market_holofoil,
                    price_market_normal,
                    price_market_reverse_holo) AS price
    FROM price_snapshots
    WHERE snapshot_date = (SELECT d FROM baseline)
)
SELECT c.card_id, c.name, c.rarity, c.number, c.set_id, c.image_url,
       n.price AS current_price,
       t.price AS price_7d_ago,
       CASE WHEN t.price > 0
            THEN ROUND((n.price - t.price) / t.price * 100, 2)
       END AS pct_change
FROM cards c
LEFT JOIN now_p  n USING (card_id)
LEFT JOIN then_p t USING (card_id)
ORDER BY CAST(c.number AS INTEGER), c.number
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history-days", type=int, default=30)
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    cards = [dict(r) for r in conn.execute(CARDS_QUERY)]

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
    set_id = cards[0]["set_id"] if cards else None
    conn.close()

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "latest_snapshot": latest,
        "set_id": set_id,
        "cards": cards,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"Wrote {len(cards)} cards -> {OUT_PATH}")


if __name__ == "__main__":
    main()