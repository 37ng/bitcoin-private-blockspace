-- Step 03: the transaction table the analysis runs on.
--
-- Two things happen here. First the in-block CPFP graph is built: an input
-- that spends a transaction confirmed in the same block is an edge from child
-- to parent. Step 04 groups those edges into packages in Python.
--
-- Second, each transaction is tested against the relay policy of its own day.
-- A transaction a default node would refuse to relay could not have reached a
-- miner through the public mempool, so it never entered the public auction and
-- must not be counted as a discount later. Every test uses the rule in force
-- on the day of the block and no other: when a release loosened a rule, what
-- the old rule would have caught counts as relayable from the release date on.
--
--   script       an output scriptPubKey matching no standard template
--   bare multisig  n > ${bare_multisig_max_n} pubkeys is non-standard
--   OP_RETURN    > ${datacarrier_limit_before} bytes of scriptPubKey in one
--                output before ${datacarrier_lift_date}, > ${datacarrier_limit_after}
--                summed over them after
--   multi OP_RETURN  more than one OP_RETURN output, before ${datacarrier_lift_date}
--   dust         an output worth less than the input that would spend it;
--                from ${ephemeral_dust_date} one is allowed on a 0-fee
--                transaction whose child spends it
--   version      outside 1..${tx_version_max_before} before ${truc_standard_date},
--                outside 1..${tx_version_max_after} after
--   TRUC         the version 3 size and topology rules, from ${truc_standard_date}
--   fee rate     < ${min_relay_before} sat/vB before ${min_relay_change_date},
--                < ${min_relay_after} sat/vB after
--   size         virtual size > ${max_standard_vsize} vB (400k WU), or under
--                ${min_standard_nonwitness_size} non-witness bytes
--   scriptSig    over ${max_standard_scriptsig_size} bytes, or not push-only
--
-- The fee-rate test carries one carve-out. A sub-minimum parent with a paying
-- child in the same block is ordinary CPFP, not a private deal — miners have
-- accepted such packages from the public mempool for years, and after Core 28
-- (${package_relay_date}) 1p1c package relay propagates them across the
-- network too. Only a sub-minimum transaction with no paying in-block child is
-- unambiguously non-relayable, so that is what `nonrelay_sub_minrelay` records.
--
-- `nonrelay_ancestor_limit` needs the ancestor closure of the CPFP graph, which
-- only exists once step 04 has built the packages. It is created FALSE here
-- and filled by step 04d, which is also the only step allowed to add to
-- `is_nonrelayable` afterwards.
CREATE OR REPLACE TABLE `${dst}.txs`
PARTITION BY block_month
CLUSTER BY block_number
OPTIONS (
  description = "Per-transaction facts, non-relayable reasons, CPFP parents, and effective fee rate."
)
AS
WITH base AS (
  SELECT
    tx_hash, block_number, block_timestamp, block_month, is_coinbase,
    fee, virtual_size, serialized_size, version, input_hashes,
    bare_multisig_max_n, op_return_count, op_return_max_bytes,
    op_return_total_bytes, nonstandard_outputs, dust_outputs,
    dust_outputs_excl_multisig, max_scriptsig_bytes, opens_with_nonpush_opcode,
    SAFE_DIVIDE(fee, virtual_size) AS raw_fee_rate,
    IF(block_timestamp < TIMESTAMP('${min_relay_change_date}'),
       ${min_relay_before}, ${min_relay_after}) AS min_relay_rate,
    -- Before Core v30 the datacarrier limit was per output; after it, the
    -- OP_RETURN scriptPubKeys of one transaction share a single budget.
    IF(block_timestamp < TIMESTAMP('${datacarrier_lift_date}'),
       op_return_max_bytes, op_return_total_bytes) AS datacarrier_bytes,
    IF(block_timestamp < TIMESTAMP('${datacarrier_lift_date}'),
       ${datacarrier_limit_before}, ${datacarrier_limit_after}) AS datacarrier_limit,
    IF(block_timestamp < TIMESTAMP('${truc_standard_date}'),
       ${tx_version_max_before}, ${tx_version_max_after}) AS max_standard_version,
    IF(block_timestamp < TIMESTAMP('${ephemeral_dust_date}'),
       ${max_dust_outputs_before}, ${max_dust_outputs_after}) AS max_dust_outputs,
    -- Non-witness bytes, from weight = 3 * base + total and
    -- virtual size = ceil(weight / 4). The rounding in virtual size makes
    -- this an upper bound, never an under-estimate, so a transaction is only
    -- called undersized when it certainly is.
    COALESCE(DIV(4 * virtual_size - serialized_size, 3),
             ${min_standard_nonwitness_size}) AS max_nonwitness_size
  FROM `${dst}.tx_base`
),
edges AS (
  -- child -> parent, both confirmed in the same block
  SELECT
    c.block_number,
    c.tx_hash AS child_hash,
    p.tx_hash AS parent_hash,
    c.raw_fee_rate AS child_rate,
    c.min_relay_rate AS child_min_relay,
    -- TRUC packages may not be mixed with ordinary ones: a version 3
    -- transaction may not spend an unconfirmed non-version-3 output, nor the
    -- other way round. The child is the transaction that gets refused.
    (c.version = ${truc_version}) != (p.version = ${truc_version}) AS truc_version_clash
  FROM base AS c
  CROSS JOIN UNNEST(c.input_hashes) AS spent
  JOIN base AS p
    ON p.tx_hash = spent
   AND p.block_number = c.block_number
  WHERE NOT c.is_coinbase
),
parent_agg AS (
  SELECT
    child_hash AS tx_hash,
    ARRAY_AGG(DISTINCT parent_hash) AS parent_hashes,
    LOGICAL_OR(truc_version_clash) AS has_truc_version_clash
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
),
with_reasons AS (
  SELECT
    b.*,
    COALESCE(p.parent_hashes, ARRAY<STRING>[]) AS parent_hashes,
    COALESCE(c.child_count, 0) AS child_count,
    COALESCE(c.has_paying_child, FALSE) AS has_paying_child,
    (p.tx_hash IS NOT NULL) AS has_in_block_parent,
    -- true when the transaction has any relative in its own block, so step 04
    -- reads only these rows into Python
    (p.tx_hash IS NOT NULL OR c.tx_hash IS NOT NULL) AS in_package,

    -- Non-relayable reasons. A coinbase transaction never has one: it is
    -- never relayed and never bids for space. Every reason is COALESCEd to
    -- FALSE, so a missing field in the source can only under-report -- a NULL
    -- reaching `is_nonrelayable` would quietly drop the transaction from the
    -- denominator in step 07b.
    COALESCE(NOT b.is_coinbase AND b.nonstandard_outputs > 0, FALSE)
      AS nonrelay_nonstandard_script,
    COALESCE(NOT b.is_coinbase AND b.bare_multisig_max_n > ${bare_multisig_max_n}, FALSE)
      AS nonrelay_bare_multisig,
    COALESCE(NOT b.is_coinbase AND b.datacarrier_bytes > b.datacarrier_limit, FALSE)
      AS nonrelay_op_return,
    COALESCE(NOT b.is_coinbase
       AND b.block_timestamp < TIMESTAMP('${datacarrier_lift_date}')
       AND b.op_return_count > 1, FALSE)
      AS nonrelay_multi_op_return,
    -- Before Core 29 no dust was allowed at all, and the test skipped bare
    -- multisig outputs. From Core 29 one dust output is allowed, but only on
    -- a transaction that pays no fee and has a child to spend the dust.
    COALESCE(NOT b.is_coinbase AND IF(
        b.block_timestamp < TIMESTAMP('${ephemeral_dust_date}'),
        b.dust_outputs_excl_multisig > b.max_dust_outputs,
        b.dust_outputs > b.max_dust_outputs
        OR (b.dust_outputs > 0
            AND NOT (COALESCE(b.fee, 0) = 0 AND COALESCE(c.child_count, 0) > 0))),
      FALSE)
      AS nonrelay_dust,
    COALESCE(NOT b.is_coinbase
       AND (b.version < ${tx_version_min} OR b.version > b.max_standard_version),
      FALSE)
      AS nonrelay_version,
    -- TRUC, from the day version 3 became standard. A version 3 transaction
    -- has its own size ceiling, a smaller one again when it spends an
    -- unconfirmed output, and may not share a package with a non-TRUC parent.
    COALESCE(NOT b.is_coinbase
       AND b.block_timestamp >= TIMESTAMP('${truc_standard_date}')
       AND ((b.version = ${truc_version} AND b.virtual_size > ${truc_max_vsize})
         OR (b.version = ${truc_version} AND p.tx_hash IS NOT NULL
             AND b.virtual_size > ${truc_child_max_vsize})
         OR COALESCE(p.has_truc_version_clash, FALSE)),
      FALSE)
      AS nonrelay_truc,
    COALESCE(NOT b.is_coinbase AND b.virtual_size > ${max_standard_vsize}, FALSE)
      AS nonrelay_oversized,
    COALESCE(NOT b.is_coinbase
       AND b.max_nonwitness_size < ${min_standard_nonwitness_size}, FALSE)
      AS nonrelay_undersized,
    COALESCE(NOT b.is_coinbase
       AND b.max_scriptsig_bytes > ${max_standard_scriptsig_size}, FALSE)
      AS nonrelay_scriptsig_size,
    COALESCE(NOT b.is_coinbase AND b.opens_with_nonpush_opcode, FALSE)
      AS nonrelay_scriptsig_nonpush,
    COALESCE(NOT b.is_coinbase AND b.raw_fee_rate < b.min_relay_rate
       AND NOT COALESCE(c.has_paying_child, FALSE), FALSE)
      AS nonrelay_sub_minrelay
  FROM base AS b
  LEFT JOIN parent_agg AS p USING (tx_hash)
  LEFT JOIN child_agg AS c USING (tx_hash)
)
SELECT
  tx_hash,
  block_number,
  block_timestamp,
  block_month,
  is_coinbase,
  fee,
  virtual_size,
  serialized_size,
  version,
  raw_fee_rate,
  min_relay_rate,
  bare_multisig_max_n,
  op_return_count,
  op_return_max_bytes,
  op_return_total_bytes,
  nonstandard_outputs,
  dust_outputs,
  max_scriptsig_bytes,
  parent_hashes,
  child_count,
  has_paying_child,
  has_in_block_parent,

  nonrelay_nonstandard_script,
  nonrelay_bare_multisig,
  nonrelay_op_return,
  nonrelay_multi_op_return,
  nonrelay_dust,
  nonrelay_version,
  nonrelay_truc,
  nonrelay_oversized,
  nonrelay_undersized,
  nonrelay_scriptsig_size,
  nonrelay_scriptsig_nonpush,
  nonrelay_sub_minrelay,
  -- Filled by step 04d, once the CPFP packages exist.
  FALSE AS nonrelay_ancestor_limit,

  (nonrelay_nonstandard_script OR nonrelay_bare_multisig OR nonrelay_op_return
   OR nonrelay_multi_op_return OR nonrelay_dust OR nonrelay_version OR nonrelay_truc
   OR nonrelay_oversized OR nonrelay_undersized OR nonrelay_scriptsig_size
   OR nonrelay_scriptsig_nonpush OR nonrelay_sub_minrelay) AS is_nonrelayable,

  in_package,

  -- A transaction with no in-block relative is its own package, so its
  -- effective rate is already known and is written here. Step 05 fills the
  -- rest from the union-find output. Coinbase stays NULL: it pays no fee and
  -- is excluded from every fee-rate statistic.
  IF(NOT in_package AND NOT is_coinbase, raw_fee_rate, NULL) AS effective_fee_rate,
  IF(NOT in_package AND NOT is_coinbase, tx_hash, NULL) AS package_id,
  IF(NOT in_package AND NOT is_coinbase, 1, NULL) AS package_tx_count,

  -- the low-fee columns, filled by step 06b
  CAST(NULL AS BOOL) AS low_fee_30,
  CAST(NULL AS BOOL) AS low_fee_50,
  CAST(NULL AS BOOL) AS low_fee_70
FROM with_reasons
