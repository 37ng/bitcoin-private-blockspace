-- Step 07b: the monthly answer.
--
-- `private_vbytes_share` is the headline: the fraction of block space in full
-- blocks that changed hands below the public price. The denominator is
-- `config.LOW_FEE_DENOMINATOR`: vbytes in full, priced blocks only, since only
-- there is a discount meaningful, and relayable only, to match a numerator
-- that can never contain non-relayable space. Non-relayable traffic is
-- reported on its own rows below, against `all_vbytes`.
CREATE OR REPLACE TABLE `${dst}.monthly_summary`
OPTIONS (description = "Private blockspace and its value, per month, at each sensitivity.")
AS
WITH full_block_totals AS (
  SELECT
    t.block_month,
    SUM(t.virtual_size) AS full_block_vbytes,
    COUNT(*) AS full_block_txs
  FROM `${dst}.txs` AS t
  JOIN `${dst}.blocks` AS b USING (block_number)
  WHERE ${low_fee_denominator} AND NOT t.is_coinbase
  GROUP BY t.block_month
),
all_totals AS (
  SELECT block_month, SUM(virtual_size) AS all_vbytes, COUNT(*) AS all_txs
  FROM `${dst}.txs`
  WHERE NOT is_coinbase
  GROUP BY block_month
),
low_fee AS (
  SELECT
    block_month,
    COUNTIF(low_fee_30) AS txs_30,
    COUNTIF(low_fee_50) AS txs_50,
    COUNTIF(low_fee_70) AS txs_70,
    SUM(IF(low_fee_30, virtual_size, 0)) AS vbytes_30,
    SUM(IF(low_fee_50, virtual_size, 0)) AS vbytes_50,
    SUM(IF(low_fee_70, virtual_size, 0)) AS vbytes_70,
    SUM(IF(low_fee_50, lower_band_sats, 0)) AS lower_band_sats_50,
    SUM(IF(low_fee_50, upper_band_sats, 0)) AS upper_band_sats_50,
    SUM(IF(low_fee_30, lower_band_sats, 0)) AS lower_band_sats_30,
    SUM(IF(low_fee_30, upper_band_sats, 0)) AS upper_band_sats_30,
    SUM(IF(low_fee_70, lower_band_sats, 0)) AS lower_band_sats_70,
    SUM(IF(low_fee_70, upper_band_sats, 0)) AS upper_band_sats_70
  FROM `${dst}.low_fee_txs`
  GROUP BY block_month
),
nonrelayable AS (
  SELECT
    block_month,
    COUNTIF(is_nonrelayable) AS nonrelayable_txs,
    SUM(IF(is_nonrelayable, virtual_size, 0)) AS nonrelayable_vbytes,
    COUNTIF(nonrelay_nonstandard_script) AS nonstandard_script_txs,
    COUNTIF(nonrelay_bare_multisig) AS bare_multisig_txs,
    COUNTIF(nonrelay_op_return) AS op_return_txs,
    COUNTIF(nonrelay_multi_op_return) AS multi_op_return_txs,
    COUNTIF(nonrelay_dust) AS dust_txs,
    COUNTIF(nonrelay_version) AS version_txs,
    COUNTIF(nonrelay_truc) AS truc_txs,
    COUNTIF(nonrelay_oversized) AS oversized_txs,
    COUNTIF(nonrelay_undersized) AS undersized_txs,
    COUNTIF(nonrelay_scriptsig_size) AS scriptsig_size_txs,
    COUNTIF(nonrelay_scriptsig_nonpush) AS scriptsig_nonpush_txs,
    COUNTIF(nonrelay_ancestor_limit) AS ancestor_limit_txs,
    COUNTIF(nonrelay_sub_minrelay) AS sub_minrelay_txs
  FROM `${dst}.txs`
  WHERE NOT is_coinbase
  GROUP BY block_month
)
SELECT
  a.block_month,
  a.all_txs,
  a.all_vbytes,
  COALESCE(fb.full_block_vbytes, 0) AS full_block_vbytes,
  COALESCE(f.txs_30, 0) AS low_fee_txs_30,
  COALESCE(f.txs_50, 0) AS low_fee_txs_50,
  COALESCE(f.txs_70, 0) AS low_fee_txs_70,
  COALESCE(f.vbytes_30, 0) AS low_fee_vbytes_30,
  COALESCE(f.vbytes_50, 0) AS low_fee_vbytes_50,
  COALESCE(f.vbytes_70, 0) AS low_fee_vbytes_70,
  SAFE_DIVIDE(f.vbytes_30, fb.full_block_vbytes) AS private_vbytes_share_30,
  SAFE_DIVIDE(f.vbytes_50, fb.full_block_vbytes) AS private_vbytes_share_50,
  SAFE_DIVIDE(f.vbytes_70, fb.full_block_vbytes) AS private_vbytes_share_70,
  COALESCE(f.lower_band_sats_50, 0) / 1e8 AS lower_band_btc_50,
  COALESCE(f.upper_band_sats_50, 0) / 1e8 AS upper_band_btc_50,
  COALESCE(f.lower_band_sats_30, 0) / 1e8 AS lower_band_btc_30,
  COALESCE(f.upper_band_sats_30, 0) / 1e8 AS upper_band_btc_30,
  COALESCE(f.lower_band_sats_70, 0) / 1e8 AS lower_band_btc_70,
  COALESCE(f.upper_band_sats_70, 0) / 1e8 AS upper_band_btc_70,
  n.nonrelayable_txs,
  n.nonrelayable_vbytes,
  SAFE_DIVIDE(n.nonrelayable_vbytes, a.all_vbytes) AS nonrelayable_vbytes_share,
  n.nonstandard_script_txs,
  n.bare_multisig_txs,
  n.op_return_txs,
  n.multi_op_return_txs,
  n.dust_txs,
  n.version_txs,
  n.truc_txs,
  n.oversized_txs,
  n.undersized_txs,
  n.scriptsig_size_txs,
  n.scriptsig_nonpush_txs,
  n.ancestor_limit_txs,
  n.sub_minrelay_txs
FROM all_totals AS a
LEFT JOIN full_block_totals AS fb USING (block_month)
LEFT JOIN low_fee AS f USING (block_month)
LEFT JOIN nonrelayable AS n USING (block_month)
ORDER BY a.block_month
