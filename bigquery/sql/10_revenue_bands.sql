-- Step 07: what the flagged space was worth.
--
-- Two bands, because the true figure is not observable on chain:
--
--   lower band  (floor - effective) x vbytes. What the buyer did not pay
--               relative to the cheapest public price in that block. The
--               minimum any private arrangement had to be worth.
--
--   upper band  block median rate x vbytes. What the same space would have
--               fetched from the middle of the public auction — the full
--               market value of what was handed over.
--
-- The truth sits between them. Neither is a measurement of a private payment,
-- which happens off chain and leaves no record here; both bound it.
CREATE OR REPLACE TABLE `${dst}.flagged_txs`
OPTIONS (description = "Flagged transactions with their block context and revenue bands.")
AS
SELECT
  t.tx_hash,
  t.block_number,
  t.block_timestamp,
  t.block_month,
  b.pool_name,
  b.pool_source,
  t.fee,
  t.virtual_size,
  t.raw_fee_rate,
  t.effective_fee_rate,
  t.package_id,
  t.package_tx_count,
  b.floor_fee_rate,
  b.median_fee_rate,
  b.weight AS block_weight,
  t.flag_a_30,
  t.flag_a_50,
  t.flag_a_70,
  GREATEST(b.floor_fee_rate - t.effective_fee_rate, 0) * t.virtual_size
    AS lower_band_sats,
  b.median_fee_rate * t.virtual_size AS upper_band_sats
FROM `${dst}.txs` AS t
JOIN `${dst}.blocks` AS b USING (block_number)
WHERE t.flag_a_70   -- the widest flag; narrower ones are subsets
