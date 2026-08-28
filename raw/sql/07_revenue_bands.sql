-- Step 07: what the low-fee space was worth.
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
CREATE OR REPLACE TABLE `${dst}.low_fee_txs`
OPTIONS (description = "Low-fee transactions with their block context and revenue bands.")
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
  t.low_fee_30,
  t.low_fee_50,
  t.low_fee_70,
  -- Both expressions come from `config.LOWER_BAND_SATS` / `UPPER_BAND_SATS`,
  -- so step 07c computes the same thing without retyping it.
  ${lower_band_sats} AS lower_band_sats,
  ${upper_band_sats} AS upper_band_sats
FROM `${dst}.txs` AS t
JOIN `${dst}.blocks` AS b USING (block_number)
WHERE t.low_fee_70   -- the widest threshold; narrower ones are subsets
