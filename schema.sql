-- Trending Pokemon Cards pipeline: storage layer
-- SQLite dialect (portable to Postgres with minor changes)

-- Static card metadata. Populated on first ingest, refreshed harmlessly after.
CREATE TABLE IF NOT EXISTS cards (
    card_id   TEXT PRIMARY KEY,   -- API id, e.g. "sv8pt5-32"
    name      TEXT NOT NULL,
    set_id    TEXT NOT NULL,
    rarity    TEXT,                -- nullable: some cards lack a rarity
    number    TEXT,                -- TEXT, not INT: promo numbers like "TG12"
    image_url TEXT
);

-- Append-only daily price facts, one row per card per day.
-- Composite PK = idempotency: re-running ingestion on the same day
-- updates that day's row instead of duplicating it.
CREATE TABLE IF NOT EXISTS price_snapshots (
    card_id                   TEXT NOT NULL REFERENCES cards(card_id),
    snapshot_date             TEXT NOT NULL,  -- ISO "YYYY-MM-DD"
    price_market_normal       REAL,           -- nullable: not every card has every variant
    price_market_holofoil     REAL,
    price_market_reverse_holo REAL,
    PRIMARY KEY (card_id, snapshot_date)
);

-- The transformation stage reads "all snapshots for a given date" —
-- this index makes that lookup cheap as history grows.
CREATE INDEX IF NOT EXISTS idx_snapshots_date
    ON price_snapshots (snapshot_date);
