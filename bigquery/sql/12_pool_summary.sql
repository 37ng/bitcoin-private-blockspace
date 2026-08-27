-- Step 07c: the same question asked per pool.
--
-- A pool that sells space outside the auction shows up here as a share of
-- flagged vbytes well above its share of block space. Read it next to
-- `sanity_check.py`: if a pool's block share does not match public hashrate
-- data, its row here is meaningless.
--
-- `full_block_vbytes` must use the same test as step 07b and step 08: full
-- AND a floor exists. A full block without a floor can hold no flagged
-- transaction, so counting it in the denominator only pushes every pool's
-- share down.
CREATE OR REPLACE TABLE `${dst}.pool_summary`
OPTIONS (description = "Flagged space and value by pool, per month and overall.")
AS
WITH per_pool AS (
  SELECT
    b.pool_name,
    t.block_month,
    COUNT(DISTINCT b.block_number) AS blocks,
    SUM(t.virtual_size) AS vbytes,
    COUNTIF(t.flag_a_50) AS flagged_txs_50,
    SUM(IF(t.flag_a_50, t.virtual_size, 0)) AS flagged_vbytes_50,
    SUM(IF(t.flag_a_30, t.virtual_size, 0)) AS flagged_vbytes_30,
    SUM(IF(t.flag_a_70, t.virtual_size, 0)) AS flagged_vbytes_70,
    SUM(IF(t.flag_a_50,
           GREATEST(b.floor_fee_rate - t.effective_fee_rate, 0) * t.virtual_size,
           0)) AS lower_band_sats_50,
    SUM(IF(t.flag_a_50, b.median_fee_rate * t.virtual_size, 0)) AS upper_band_sats_50,
    SUM(IF(b.is_full AND b.floor_fee_rate IS NOT NULL, t.virtual_size, 0))
      AS full_block_vbytes
  FROM `${dst}.txs` AS t
  JOIN `${dst}.blocks` AS b USING (block_number)
  WHERE NOT t.is_coinbase
  GROUP BY b.pool_name, t.block_month
)
SELECT
  pool_name,
  block_month,
  blocks,
  vbytes,
  full_block_vbytes,
  flagged_txs_50,
  flagged_vbytes_30,
  flagged_vbytes_50,
  flagged_vbytes_70,
  SAFE_DIVIDE(flagged_vbytes_50, full_block_vbytes) AS flagged_share_of_full_50,
  lower_band_sats_50 / 1e8 AS lower_band_btc_50,
  upper_band_sats_50 / 1e8 AS upper_band_btc_50
FROM per_pool
ORDER BY block_month, flagged_vbytes_50 DESC
