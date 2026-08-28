-- Step 01: the one expensive pass over the public dataset.
--
-- Everything the pipeline needs from `crypto_bitcoin.transactions` is pulled
-- here and never read again: the identity of each transaction, what it paid,
-- what it spent (for the in-block CPFP graph), and the shape of its outputs
-- and inputs, which is what decides relayability. Later steps read this local
-- table instead, which costs a fraction of the source scan.
--
-- The output types this dataset labels are not the ones relay policy uses, so
-- every scriptPubKey is classified here from its raw hex against the same
-- templates Core's `Solver` matches: P2PK, P2PKH, P2SH, bare multisig,
-- OP_RETURN, and witness programs. An output matching none of them is
-- non-standard and its transaction could not be relayed.
--
-- The classification is deliberately generous where hex alone cannot settle
-- the question. An OP_RETURN payload is taken as standard without checking
-- that it is push-only, and a multisig template is taken as standard without
-- counting its pubkeys against the declared n. Both of those let a
-- non-standard output through as standard. Neither can flag a relayable
-- transaction, which is the error that would matter: a false flag deletes a
-- real transaction from the measurement.
CREATE OR REPLACE TABLE `${dst}.tx_base`
PARTITION BY block_month
CLUSTER BY block_number
OPTIONS (
  description = "Raw per-transaction extract from crypto_bitcoin, ${start_date} onward."
)
AS
WITH scanned AS (
  SELECT
    t.`hash` AS tx_hash,
    t.block_number,
    t.block_timestamp,
    t.block_timestamp_month AS block_month,
    t.is_coinbase,
    CAST(t.fee AS INT64) AS fee,          -- satoshis
    t.virtual_size,                        -- vbytes
    t.`size` AS serialized_size,           -- bytes, witness included
    COALESCE(t.version, ${tx_version_min}) AS version,

    -- Hashes this transaction spends. A parent in the same block makes a CPFP
    -- edge; the join that finds those happens in step 03.
    ARRAY(
      SELECT DISTINCT i.spent_transaction_hash
      FROM UNNEST(t.inputs) AS i
      WHERE i.spent_transaction_hash IS NOT NULL
    ) AS input_hashes,

    -- Payout addresses, kept for coinbase transactions only. Step 02 uses
    -- them as the fallback route to a pool name.
    IF(t.is_coinbase,
       ARRAY(SELECT DISTINCT a FROM UNNEST(t.outputs) AS o, UNNEST(o.addresses) AS a),
       ARRAY<STRING>[]) AS coinbase_addresses,

    -- One pass over the outputs, classifying each scriptPubKey once and
    -- reducing to the counts the relay rules ask about.
    (
      SELECT AS STRUCT
        -- Largest n over the bare multisig outputs (0 when there are none).
        COALESCE(MAX(IF(is_multisig, multisig_n, 0)), 0) AS bare_multisig_max_n,
        COUNTIF(is_op_return) AS op_return_count,
        -- The datacarrier limit applies to the whole scriptPubKey, not the
        -- payload: per output before Core v30, summed over them after.
        COALESCE(MAX(IF(is_op_return, script_bytes, 0)), 0) AS op_return_max_bytes,
        COALESCE(SUM(IF(is_op_return, script_bytes, 0)), 0) AS op_return_total_bytes,
        COUNTIF(NOT is_standard_script) AS nonstandard_outputs,
        COUNTIF(is_dust) AS dust_outputs,
        -- Before Core 29 the dust test skipped OP_RETURN and bare multisig
        -- outputs; from Core 29 on it runs over every output.
        COUNTIF(is_dust AND NOT is_multisig) AS dust_outputs_excl_multisig
      FROM (
        SELECT
          is_op_return,
          is_multisig,
          multisig_n,
          script_bytes,
          -- Core's `Solver` returns NONSTANDARD for anything that matches no
          -- template. A witness program of an unknown version is standard to
          -- create (only spending one is not), so it is standard here.
          (is_p2pkh OR is_p2sh OR is_p2pk OR is_op_return
           OR (is_multisig AND multisig_m <= multisig_n)
           OR (is_witness_program
               AND (witness_version > 0 OR witness_program_bytes IN (20, 32)))
          ) AS is_standard_script,
          -- Dust: worth less than the input that would spend it, priced at
          -- the dust relay fee. An unspendable output is never dust.
          value < IF(
            is_op_return OR script_bytes > ${max_script_size},
            0,
            DIV(${dust_relay_fee} * (
                  8                                          -- the value field
                  + CASE WHEN script_bytes < 253 THEN 1
                         WHEN script_bytes < 65536 THEN 3
                         ELSE 5 END                          -- the length prefix
                  + script_bytes
                  + IF(is_witness_program,
                       ${dust_spend_cost_witness}, ${dust_spend_cost_legacy})
                ), 1000)
          ) AS is_dust
        FROM (
          SELECT
            CAST(o.value AS INT64) AS value,
            DIV(COALESCE(LENGTH(o.script_hex), 0), 2) AS script_bytes,
            -- OP_RETURN. Whether the rest is push-only is not checked; see
            -- the note at the top of this file.
            COALESCE(STARTS_WITH(o.script_hex, '6a'), FALSE) AS is_op_return,
            REGEXP_CONTAINS(o.script_hex, r'^76a914[0-9a-f]{40}88ac\z') AS is_p2pkh,
            REGEXP_CONTAINS(o.script_hex, r'^a914[0-9a-f]{40}87\z') AS is_p2sh,
            -- A pubkey push whose header byte agrees with its length, which
            -- is what `CPubKey::ValidSize` asks: 02/03 for the 33-byte form,
            -- 04/06/07 for the 65-byte one.
            REGEXP_CONTAINS(
              o.script_hex,
              r'^(21(02|03)[0-9a-f]{64}|41(04|06|07)[0-9a-f]{128})ac\z') AS is_p2pk,
            -- Bare multisig: OP_m <pubkey>.. OP_n OP_CHECKMULTISIG, with
            -- OP_1..OP_16 encoded 0x51..0x60. The full-script anchor keeps
            -- 32-byte taproot outputs (0x5120..) out.
            REGEXP_CONTAINS(
              o.script_hex,
              r'^(5[1-9a-f]|60)(21(02|03)[0-9a-f]{64}|41(04|06|07)[0-9a-f]{128})+(5[1-9a-f]|60)ae\z'
            ) AS is_multisig,
            COALESCE(
              TO_CODE_POINTS(FROM_HEX(SUBSTR(o.script_hex, 1, 2)))[SAFE_OFFSET(0)] - 80,
              0) AS multisig_m,
            COALESCE(
              TO_CODE_POINTS(FROM_HEX(SUBSTR(o.script_hex, -4, 2)))[SAFE_OFFSET(0)] - 80,
              0) AS multisig_n,
            -- Witness program: OP_0 or OP_1..OP_16, then a 2..40 byte push
            -- that is the whole rest of the script.
            COALESCE(
              DIV(LENGTH(o.script_hex), 2) BETWEEN 4 AND 42
              AND (TO_CODE_POINTS(FROM_HEX(SUBSTR(o.script_hex, 1, 2)))[SAFE_OFFSET(0)] = 0
                   OR TO_CODE_POINTS(FROM_HEX(SUBSTR(o.script_hex, 1, 2)))[SAFE_OFFSET(0)]
                      BETWEEN 81 AND 96)
              AND TO_CODE_POINTS(FROM_HEX(SUBSTR(o.script_hex, 3, 2)))[SAFE_OFFSET(0)] + 2
                  = DIV(LENGTH(o.script_hex), 2),
              FALSE) AS is_witness_program,
            COALESCE(
              GREATEST(
                TO_CODE_POINTS(FROM_HEX(SUBSTR(o.script_hex, 1, 2)))[SAFE_OFFSET(0)] - 80,
                0),
              0) AS witness_version,
            COALESCE(
              TO_CODE_POINTS(FROM_HEX(SUBSTR(o.script_hex, 3, 2)))[SAFE_OFFSET(0)],
              0) AS witness_program_bytes
          FROM UNNEST(t.outputs) AS o
        )
      )
    ) AS outs,

    -- The same for the inputs. A scriptSig must fit the standard size and
    -- must be push-only; only the first opcode of the push-only test can be
    -- read from hex without walking the script, so that is what is recorded.
    -- It under-reports and never over-reports.
    (
      SELECT AS STRUCT
        COALESCE(MAX(DIV(COALESCE(LENGTH(i.script_hex), 0), 2)), 0) AS max_scriptsig_bytes,
        COALESCE(LOGICAL_OR(COALESCE(
          TO_CODE_POINTS(FROM_HEX(SUBSTR(i.script_hex, 1, 2)))[SAFE_OFFSET(0)] > 96,
          FALSE)), FALSE) AS opens_with_nonpush_opcode
      FROM UNNEST(t.inputs) AS i
    ) AS ins

  FROM `${src}.transactions` AS t
  WHERE t.block_timestamp_month BETWEEN DATE '${start_month}' AND DATE '${end_month}'
    AND t.block_timestamp >= TIMESTAMP('${start_date}')
    AND t.block_timestamp <  TIMESTAMP('${end_date}')
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
  input_hashes,
  coinbase_addresses,
  outs.bare_multisig_max_n,
  outs.op_return_count,
  outs.op_return_max_bytes,
  outs.op_return_total_bytes,
  outs.nonstandard_outputs,
  outs.dust_outputs,
  outs.dust_outputs_excl_multisig,
  ins.max_scriptsig_bytes,
  ins.opens_with_nonpush_opcode
FROM scanned
