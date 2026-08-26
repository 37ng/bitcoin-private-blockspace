-- Step 05: the fee floor of each block, derived from its neighbours.
--
-- The p05 of a block's effective rates is the price at the cheap end of what
-- that block actually sold. But a block cannot be judged against itself: a
-- pool that stuffs its own block with free transactions would drag its own p05
-- down and hide the very thing being measured. So the floor of block b is the
-- median of the p05 of b-3, b-2, b-1, b+1, b+2, b+3 — six values, never b
-- itself, median as the average of the middle two.
--
-- All six neighbours must have a p05 or the floor is NULL and the block takes
-- no part in the flagging. That happens only at the two ends of the window.
--
-- The percentile runs over relayable transactions. A non-relayable transaction
-- never entered the public auction, so leaving it in would let the traffic
-- under investigation set the price it is measured against. `p05_all` keeps
-- the unfiltered value so the difference stays visible.
CREATE OR REPLACE TABLE `${dst}.block_percentiles`
OPTIONS (description = "Per-block effective fee rate percentiles.")
AS
SELECT
  block_number,
  ANY_VALUE(p05_relayable) AS p05_fee_rate,
  ANY_VALUE(p05_all) AS p05_all,
  ANY_VALUE(p50_relayable) AS median_fee_rate,
  COUNT(*) AS priced_tx_count
FROM (
  SELECT
    block_number,
    PERCENTILE_CONT(IF(is_nonrelayable, NULL, effective_fee_rate), ${floor_percentile} IGNORE NULLS)
      OVER (PARTITION BY block_number) AS p05_relayable,
    PERCENTILE_CONT(effective_fee_rate, ${floor_percentile} IGNORE NULLS)
      OVER (PARTITION BY block_number) AS p05_all,
    PERCENTILE_CONT(IF(is_nonrelayable, NULL, effective_fee_rate), 0.5 IGNORE NULLS)
      OVER (PARTITION BY block_number) AS p50_relayable
  FROM `${dst}.txs`
  WHERE NOT is_coinbase
    AND effective_fee_rate IS NOT NULL
)
GROUP BY block_number
