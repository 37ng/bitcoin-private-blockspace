-- Step 07c: the same question asked per pool.
--
-- A pool that sells space outside the auction shows up here as a share of
-- flagged vbytes well above its share of block space. Read it next to
-- `sanity_check.py`: if a pool's block share does not match public hashrate
-- data, its row here is meaningless.
--
-- The denominator is `config.COUNTABLE_SPACE`, the same test step 07b uses.
-- A full block without a floor, and any non-relayable transaction, can hold or
-- be no flagged transaction, so counting either here would only push every
-- pool's share down.
CREATE OR REPLACE TABLE `${dst}.pool_summary`
OPTIONS (description = "Flagged space and value by pool, per month and overall.")
AS
WITH per_pool AS (
  SELECT
    b.pool_name,
    t.block_month,
    COUNT(DISTINCT b.block_number) AS blocks,
    SUM(t.virtual_size) AS vbytes,
    COUNTIF(t.low_fee_50) AS flagged_txs_50,
    SUM(IF(t.low_fee_50, t.virtual_size, 0)) AS flagged_vbytes_50,
    SUM(IF(t.low_fee_30, t.virtual_size, 0)) AS flagged_vbytes_30,
    SUM(IF(t.low_fee_70, t.virtual_size, 0)) AS flagged_vbytes_70,
    SUM(IF(t.low_fee_50, ${lower_band_sats}, 0)) AS lower_band_sats_50,
    SUM(IF(t.low_fee_50, ${upper_band_sats}, 0)) AS upper_band_sats_50,
    SUM(IF(${countable_space}, t.virtual_size, 0)) AS full_block_vbytes
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
