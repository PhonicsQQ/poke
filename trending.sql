-- Transformation layer: which cards are trending?
-- A VIEW, not a table: recomputed on every read, so it can never be stale
-- and there's no second job to schedule.

DROP VIEW IF EXISTS trending;

CREATE VIEW trending AS
WITH
-- The most recent snapshot day we have.
latest AS (
    SELECT MAX(snapshot_date) AS d FROM price_snapshots
),

-- The comparison day: ideally exactly 7 days before `latest`, but if
-- history is shorter than that (early days of the pipeline), fall back
-- to the oldest snapshot within the window. With 1 day of data this
-- equals `latest` and every pct_change is 0 -- still lets you verify
-- the pipeline end-to-end on day one.
baseline AS (
    SELECT MIN(snapshot_date) AS d
    FROM price_snapshots
    WHERE snapshot_date >= date((SELECT d FROM latest), '-7 day')
),

-- One representative market price per card per day. COALESCE order
-- reflects how collectors value variants: holofoil is the "main" price
-- for chase cards, normal for commons, reverse holo as last resort.
now_prices AS (
    SELECT card_id,
           COALESCE(price_market_holofoil,
                    price_market_normal,
                    price_market_reverse_holo) AS price
    FROM price_snapshots
    WHERE snapshot_date = (SELECT d FROM latest)
),
then_prices AS (
    SELECT card_id,
           COALESCE(price_market_holofoil,
                    price_market_normal,
                    price_market_reverse_holo) AS price
    FROM price_snapshots
    WHERE snapshot_date = (SELECT d FROM baseline)
)

SELECT
    c.card_id,
    c.name,
    c.image_url,
    n.price                                            AS current_price,
    t.price                                            AS price_7d_ago,
    ROUND((n.price - t.price) / t.price * 100, 2)      AS pct_change,
    RANK() OVER (
        ORDER BY (n.price - t.price) / t.price DESC,  -- primary: % change
                 n.price DESC,                        -- tiebreak: bigger base price
                 c.card_id                            -- final: guaranteed unique
    )                                                  AS trend_rank
FROM now_prices n
JOIN then_prices t USING (card_id)
JOIN cards c       USING (card_id)
WHERE n.price IS NOT NULL
  AND t.price IS NOT NULL
  AND t.price >= 1.00;   -- noise floor: without it, a $0.02 -> $0.05 common
                         -- posts +150% and buries every real mover. Tune to taste.