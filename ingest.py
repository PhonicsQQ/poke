"""
Daily ingestion for the Trending Pokemon Cards pipeline.

Flow:
  1. Fetch all cards in one set from the Pokemon TCG API (handles pagination).
  2. Archive the raw JSON response to data/raw/  (replayable "bronze layer").
  3. Upsert static metadata into `cards`.
  4. Upsert today's market prices into `price_snapshots`.

Re-running on the same day is safe: the composite primary key on
(card_id, snapshot_date) plus ON CONFLICT DO UPDATE makes step 4 idempotent.

Usage:
    python ingest.py                      # default set
    python ingest.py --set-id sv8         # any set id
    POKEMONTCG_API_KEY=xxx python ingest.py   # optional key, higher rate limits
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

import requests

API_URL = "https://api.pokemontcg.io/v2/cards"
PAGE_SIZE = 250  # API max; most sets fit in one page

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DB = SCRIPT_DIR / "pokeprices.db"
RAW_DIR = SCRIPT_DIR / "data" / "raw"
SCHEMA_PATH = SCRIPT_DIR / "schema.sql"


# ---------------------------------------------------------------- extraction

def fetch_set_cards(set_id: str, api_key: str | None = None) -> list[dict]:
    """Fetch every card in a set, following pagination."""
    headers = {"X-Api-Key": api_key} if api_key else {}
    cards: list[dict] = []
    page = 1
    while True:
        resp = requests.get(
            API_URL,
            params={"q": f"set.id:{set_id}", "page": page, "pageSize": PAGE_SIZE},
            headers=headers,
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json()
        cards.extend(payload["data"])
        # totalCount tells us when we've seen everything
        if page * payload["pageSize"] >= payload["totalCount"]:
            break
        page += 1
        time.sleep(1)  # stay polite to the free tier
    return cards


def archive_raw(cards: list[dict], set_id: str, snapshot_date: str) -> Path:
    """Save the raw API payload so any day can be re-parsed later."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{set_id}_{snapshot_date}.json"
    path.write_text(json.dumps(cards, indent=2))
    return path


# ------------------------------------------------------------ transformation

def market_price(card: dict, variant: str) -> float | None:
    """Safely dig out tcgplayer.prices.<variant>.market; None if absent.

    Any level can be missing: cards with no TCGplayer listing, variants
    the card wasn't printed in, or listings with no recent sales.
    """
    tcgplayer = card.get("tcgplayer") or {}
    prices = tcgplayer.get("prices") or {}
    variant_prices = prices.get(variant) or {}
    return variant_prices.get("market")


def card_row(card: dict) -> tuple:
    """Map an API card object to a `cards` table row."""
    return (
        card["id"],
        card["name"],
        card["set"]["id"],
        card.get("rarity"),
        card.get("number"),
        (card.get("images") or {}).get("small"),
    )


def snapshot_row(card: dict, snapshot_date: str) -> tuple:
    """Map an API card object to a `price_snapshots` row for today."""
    return (
        card["id"],
        snapshot_date,
        market_price(card, "normal"),
        market_price(card, "holofoil"),
        market_price(card, "reverseHolofoil"),
    )


# --------------------------------------------------------------------- load

def load(cards: list[dict], snapshot_date: str, db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_PATH.read_text())  # idempotent (IF NOT EXISTS)

        conn.executemany(
            """
            INSERT INTO cards (card_id, name, set_id, rarity, number, image_url)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (card_id) DO UPDATE SET
                name = excluded.name,
                rarity = excluded.rarity,
                image_url = excluded.image_url
            """,
            [card_row(c) for c in cards],
        )

        conn.executemany(
            """
            INSERT INTO price_snapshots (
                card_id, snapshot_date,
                price_market_normal, price_market_holofoil, price_market_reverse_holo
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (card_id, snapshot_date) DO UPDATE SET
                price_market_normal       = excluded.price_market_normal,
                price_market_holofoil     = excluded.price_market_holofoil,
                price_market_reverse_holo = excluded.price_market_reverse_holo
            """,
            [snapshot_row(c, snapshot_date) for c in cards],
        )

        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest daily card prices.")
    parser.add_argument("--set-id", default="sv8pt5",
                        help="Pokemon TCG API set id (default: sv8pt5, Prismatic Evolutions)")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path")
    args = parser.parse_args()

    api_key = os.environ.get("POKEMONTCG_API_KEY")
    snapshot_date = date.today().isoformat()

    print(f"Fetching set {args.set_id} ...")
    cards = fetch_set_cards(args.set_id, api_key)
    if not cards:
        print(f"No cards returned for set '{args.set_id}' — check the set id.",
              file=sys.stderr)
        return 1

    raw_path = archive_raw(cards, args.set_id, snapshot_date)
    print(f"Archived raw payload -> {raw_path}")

    load(cards, snapshot_date, Path(args.db))
    priced = sum(
        1 for c in cards
        if any(market_price(c, v) is not None
               for v in ("normal", "holofoil", "reverseHolofoil"))
    )
    print(f"Loaded {len(cards)} cards, {priced} with at least one market price, "
          f"snapshot date {snapshot_date}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
