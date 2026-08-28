-- Out-of-band spend through mempool.space, per month.
--
-- Lives in `${accel_dst}`, not the pipeline's `${dst}`: this is not part of
-- the measurement, it is a labelled sample of confirmed out-of-auction
-- purchases, used to calibrate the pipeline and to bound what the public
-- broker explains.
--
-- Every acceleration that completed and was mined counts, cancelled or not.
-- A cancellation is not a rollback: once a partner pool has the transaction in
-- a block, the payment is owed, so 28 `completed`+`canceled` records are real
-- money. Only `failed` is excluded -- there the acceleration never happened,
-- including the 62 whose transaction was mined anyway. `status` carries both
-- `completed` and `completed_provisional`, so the test is a prefix.
--
-- fee_delta is the sum credited to the miner. bid_boost belongs to the v2
-- bidding model and is a different quantity -- both are kept so the totals can
-- be compared against the published /accelerations/stats figures rather than
-- assumed equal to them.
CREATE OR REPLACE TABLE `${accel_dst}.acceleration_monthly` AS
SELECT
  DATE_TRUNC(DATE(added), MONTH)              AS month,
  COUNT(*)                                    AS n_accelerations,
  SUM(fee_delta)                              AS off_chain_sats,
  SUM(fee_delta) / 100000000                  AS off_chain_btc,
  SUM(bid_boost)                              AS bid_boost_sats,
  SUM(effective_fee)                          AS on_chain_sats,
  SUM(effective_vsize)                        AS vsize,
  SAFE_DIVIDE(SUM(fee_delta), SUM(effective_vsize))     AS off_chain_sat_vb,
  SAFE_DIVIDE(SUM(effective_fee), SUM(effective_vsize)) AS on_chain_sat_vb
FROM `${accel_dst}.accelerations`
WHERE STARTS_WITH(status, 'completed')
  AND block_height IS NOT NULL
GROUP BY month
ORDER BY month;
