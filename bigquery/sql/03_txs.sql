-- Step 03: the transaction table the analysis runs on.
--
-- Two things happen here. First the in-block CPFP graph is built: an input
-- that spends a transaction confirmed in the same block is an edge from child
-- to parent. Step 04 groups those edges into packages in Python.
--
-- Second, each transaction is tested against the relay policy of its own day.
-- A transaction a default node would refuse to relay could not have reached a
-- miner through the public mempool, so it never entered the public auction and
-- must not be counted as a discount later.
--
--   bare multisig  n > ${bare_multisig_max_n} pubkeys is non-standard
--   OP_RETURN      > ${datacarrier_limit_before} bytes of scriptPubKey before
--                  ${datacarrier_lift_date}, > ${datacarrier_limit_after} after
--   fee rate       < ${min_relay_before} sat/vB before ${min_relay_change_date},
--                  < ${min_relay_after} sat/vB after
--   size           virtual size > ${max_standard_vsize} vB (400k WU)
--
-- The fee-rate test carries one carve-out. A sub-minimum parent with a paying
-- child in the same block is ordinary CPFP, not a private deal — miners have
-- accepted such packages from the public mempool for years, and after Core 28
-- (${package_relay_date}) 1p1c package relay propagates them across the
-- network too. Only a sub-minimum transaction with no paying in-block child is
-- unambiguously non-relayable, so that is what `flag_sub_minrelay` records.
CREATE OR REPLACE TABLE `${dst}.txs`
PARTITION BY block_month
CLUSTER BY block_number
OPTIONS (
  description = "Per-transaction facts, relay flags, CPFP parents, and effective fee rate."
)
AS
WITH base AS (
  SELECT
    tx_hash, block_number, block_timestamp, block_month, is_coinbase,
    fee, virtual_size, input_hashes, bare_multisig_max_n, op_return_max_bytes,
    SAFE_DIVIDE(fee, virtual_size) AS raw_fee_rate,
    IF(block_timestamp < TIMESTAMP('${min_relay_change_date}'),
       ${min_relay_before}, ${min_relay_after}) AS min_relay_rate,
    IF(block_timestamp < TIMESTAMP('${datacarrier_lift_date}'),
       ${datacarrier_limit_before}, ${datacarrier_limit_after}) AS datacarrier_limit
  FROM `${dst}.tx_base`
),
edges AS (
  -- child -> parent, both confirmed in the same block
  SELECT
    c.block_number,
    c.tx_hash AS child_hash,
    p.tx_hash AS parent_hash,
    c.raw_fee_rate AS child_rate,
    c.min_relay_rate AS child_min_relay
  FROM base AS c
  CROSS JOIN UNNEST(c.input_hashes) AS spent
  JOIN base AS p
    ON p.tx_hash = spent
   AND p.block_number = c.block_number
  WHERE NOT c.is_coinbase
),
parent_agg AS (
  SELECT child_hash AS tx_hash, ARRAY_AGG(DISTINCT parent_hash) AS parent_hashes
  FROM edges
  GROUP BY tx_hash
),
child_agg AS (
  SELECT
    parent_hash AS tx_hash,
    COUNT(*) AS child_count,
    LOGICAL_OR(child_rate >= child_min_relay) AS has_paying_child
  FROM edges
  GROUP BY tx_hash
)
SELECT
  b.tx_hash,
  b.block_number,
  b.block_timestamp,
  b.block_month,
  b.is_coinbase,
  b.fee,
  b.virtual_size,
  b.raw_fee_rate,
  b.min_relay_rate,
  b.bare_multisig_max_n,
  b.op_return_max_bytes,
  COALESCE(p.parent_hashes, ARRAY<STRING>[]) AS parent_hashes,
  COALESCE(c.child_count, 0) AS child_count,
  COALESCE(c.has_paying_child, FALSE) AS has_paying_child,

  -- non-relayable flags (a coinbase transaction is never flagged)
  (NOT b.is_coinbase AND b.bare_multisig_max_n > ${bare_multisig_max_n})
    AS flag_bare_multisig,
  (NOT b.is_coinbase AND b.op_return_max_bytes > b.datacarrier_limit)
    AS flag_op_return,
  (NOT b.is_coinbase AND b.virtual_size > ${max_standard_vsize})
    AS flag_oversized,
  (NOT b.is_coinbase AND b.raw_fee_rate < b.min_relay_rate
     AND NOT COALESCE(c.has_paying_child, FALSE))
    AS flag_sub_minrelay,
  (NOT b.is_coinbase AND (
      b.bare_multisig_max_n > ${bare_multisig_max_n}
   OR b.op_return_max_bytes > b.datacarrier_limit
   OR b.virtual_size > ${max_standard_vsize}
   OR (b.raw_fee_rate < b.min_relay_rate AND NOT COALESCE(c.has_paying_child, FALSE))
  )) AS is_nonrelayable,

  -- true when the transaction has any relative in its own block, so step 04
  -- reads only these rows into Python
  (p.tx_hash IS NOT NULL OR c.tx_hash IS NOT NULL) AS in_package,

  -- A transaction with no in-block relative is its own package, so its
  -- effective rate is already known and is written here. Step 05 fills the
  -- rest from the union-find output. Coinbase stays NULL: it pays no fee and
  -- is excluded from every fee-rate statistic.
  IF(p.tx_hash IS NULL AND c.tx_hash IS NULL AND NOT b.is_coinbase,
     b.raw_fee_rate, NULL) AS effective_fee_rate,
  IF(p.tx_hash IS NULL AND c.tx_hash IS NULL AND NOT b.is_coinbase,
     b.tx_hash, NULL) AS package_id,
  IF(p.tx_hash IS NULL AND c.tx_hash IS NULL AND NOT b.is_coinbase,
     1, NULL) AS package_tx_count,

  -- the low-fee flags, filled by step 06b
  CAST(NULL AS BOOL) AS low_fee_30,
  CAST(NULL AS BOOL) AS low_fee_50,
  CAST(NULL AS BOOL) AS low_fee_70
FROM base AS b
LEFT JOIN parent_agg AS p USING (tx_hash)
LEFT JOIN child_agg AS c USING (tx_hash)
