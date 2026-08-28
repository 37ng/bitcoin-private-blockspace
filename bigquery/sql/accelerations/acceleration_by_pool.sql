-- Out-of-band spend per pool, using this project's own attribution.
--
-- Requires `${dst}.blocks` from step 02, so run it after the main pipeline.
-- The API also carries `mined_by_pool_unique_id`, but that is mempool.space's
-- pool id and it is null on some records; joining on block height through our
-- own attribution keeps one definition of "which pool" across the project.
-- The join reaches across datasets -- `a` from `${accel_dst}`, `b` from
-- `${dst}` -- and the result is written back into `${accel_dst}` with the
-- rest of the acceleration tables.
--
-- `pools_offered` counts how many partner pools the request went to. It is a
-- coverage measure: an acceleration only works if a partner pool wins the
-- block, so a low count means the payment was a bet, not a purchase.
CREATE OR REPLACE TABLE `${accel_dst}.acceleration_by_pool` AS
SELECT
  b.pool_name,
  COUNT(*)                                  AS n_accelerations,
  SUM(a.fee_delta)                          AS off_chain_sats,
  SUM(a.fee_delta) / 100000000              AS off_chain_btc,
  SUM(a.effective_vsize)                    AS vsize,
  SAFE_DIVIDE(SUM(a.fee_delta), SUM(a.effective_vsize))     AS off_chain_sat_vb,
  SAFE_DIVIDE(SUM(a.effective_fee), SUM(a.effective_vsize)) AS on_chain_sat_vb,
  AVG(ARRAY_LENGTH(a.pools))                AS avg_pools_offered,
  MIN(a.block_height)                       AS first_block,
  MAX(a.block_height)                       AS last_block
FROM `${accel_dst}.accelerations` AS a
JOIN `${dst}.blocks` AS b
  ON b.block_number = a.block_height
-- Cancelled-but-completed accelerations count: see acceleration_monthly.sql.
WHERE STARTS_WITH(a.status, 'completed')
GROUP BY b.pool_name
ORDER BY off_chain_sats DESC;
