-- Step 01: the one expensive pass over the public dataset.
--
-- Everything the pipeline needs from `crypto_bitcoin.transactions` is pulled
-- here and never read again: the identity of each transaction, what it paid,
-- what it spent (for the in-block CPFP graph), and the two output shapes that
-- decide relayability. Later steps read this local table instead, which costs
-- a fraction of the source scan.
--
-- Output types in this dataset: OP_RETURN and bare multisig outputs are both
-- labelled 'nonstandard', so both are detected from the raw scriptPubKey hex.
CREATE OR REPLACE TABLE `${dst}.tx_base`
PARTITION BY block_month
CLUSTER BY block_number
OPTIONS (
  description = "Raw per-transaction extract from crypto_bitcoin, ${start_date} onward."
)
AS
SELECT
  t.`hash` AS tx_hash,
  t.block_number,
  t.block_timestamp,
  t.block_timestamp_month AS block_month,
  t.is_coinbase,
  CAST(t.fee AS INT64) AS fee,          -- satoshis
  t.virtual_size,                        -- vbytes

  -- Hashes this transaction spends. A parent in the same block makes a CPFP
  -- edge; the join that finds those happens in step 03.
  ARRAY(
    SELECT DISTINCT i.spent_transaction_hash
    FROM UNNEST(t.inputs) AS i
    WHERE i.spent_transaction_hash IS NOT NULL
  ) AS input_hashes,

  -- Largest n over the bare multisig outputs (0 when there are none).
  -- A bare multisig scriptPubKey is OP_m <pubkey>.. OP_n OP_CHECKMULTISIG,
  -- with OP_1..OP_16 encoded 0x51..0x60 and pubkeys pushed with 0x21 or 0x41.
  -- The full-script anchor keeps 32-byte taproot outputs (0x5120..) out.
  (
    SELECT COALESCE(MAX(TO_CODE_POINTS(FROM_HEX(SUBSTR(o.script_hex, -4, 2)))[OFFSET(0)] - 80), 0)
    FROM UNNEST(t.outputs) AS o
    WHERE REGEXP_CONTAINS(
      o.script_hex,
      r'^(5[1-9a-f]|60)(21[0-9a-f]{66}|41[0-9a-f]{130})+(5[1-9a-f]|60)ae\z')
  ) AS bare_multisig_max_n,

  -- Size in bytes of the largest OP_RETURN scriptPubKey (0 when there is none).
  -- The datacarrier limit applies to the whole scriptPubKey, not the payload.
  (
    SELECT COALESCE(MAX(CAST(LENGTH(o.script_hex) / 2 AS INT64)), 0)
    FROM UNNEST(t.outputs) AS o
    WHERE STARTS_WITH(o.script_hex, '6a')
  ) AS op_return_max_bytes,

  -- Payout addresses, kept for coinbase transactions only. Step 02 uses them
  -- as the fallback route to a pool name.
  IF(t.is_coinbase,
     ARRAY(SELECT DISTINCT a FROM UNNEST(t.outputs) AS o, UNNEST(o.addresses) AS a),
     ARRAY<STRING>[]) AS coinbase_addresses

FROM `${src}.transactions` AS t
WHERE t.block_timestamp_month BETWEEN DATE '${start_month}' AND DATE '${end_month}'
  AND t.block_timestamp >= TIMESTAMP('${start_date}')
  AND t.block_timestamp <  TIMESTAMP('${end_date}')
