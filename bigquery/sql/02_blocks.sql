-- Step 02: blocks, with one job — tie a block to the pool that mined it.
--
-- A block header names no miner. Attribution reads the two marks a pool leaves
-- in its own coinbase transaction: a tag in the coinbase scriptSig, and the
-- payout address of a coinbase output. The tag wins where both are present;
-- the longest matching tag wins among tags, so a specific brand beats a
-- generic substring.
--
-- `floor_fee_rate` is created empty here and filled by step 06. The other
-- empty columns are filled by step 07. The table holds one row per block
-- (~180k rows for the window), which is far below 1 GB, so it is neither
-- partitioned nor clustered.
CREATE OR REPLACE TABLE `${dst}.blocks`
OPTIONS (
  description = "One row per block: pool attribution, fullness, and the neighbour-derived fee floor."
)
AS
WITH coinbase AS (
  SELECT block_number, ANY_VALUE(coinbase_addresses) AS coinbase_addresses
  FROM `${dst}.tx_base`
  WHERE is_coinbase
  GROUP BY block_number
),
raw AS (
  SELECT
    b.number AS block_number,
    b.`hash` AS block_hash,
    b.timestamp AS block_timestamp,
    DATE(b.timestamp) AS block_date,
    DATE_TRUNC(DATE(b.timestamp), MONTH) AS block_month,
    b.weight,
    b.size AS block_size,
    b.transaction_count,
    -- coinbase_param is hex; pool tags are ASCII inside it
    COALESCE(SAFE_CONVERT_BYTES_TO_STRING(FROM_HEX(b.coinbase_param)), '') AS coinbase_text,
    COALESCE(c.coinbase_addresses, ARRAY<STRING>[]) AS coinbase_addresses
  FROM `${src}.blocks` AS b
  LEFT JOIN coinbase AS c ON c.block_number = b.number
  WHERE b.timestamp_month BETWEEN DATE '${start_month}' AND DATE '${end_month}'
    AND b.timestamp >= TIMESTAMP('${start_date}')
    AND b.timestamp <  TIMESTAMP('${end_date}')
),
by_tag AS (
  SELECT r.block_number, t.pool_name
  FROM raw AS r
  CROSS JOIN UNNEST(${pool_tags}) AS t
  WHERE STRPOS(LOWER(r.coinbase_text), t.tag) > 0  -- tags arrive lowercased
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY r.block_number ORDER BY LENGTH(t.tag) DESC, t.pool_name) = 1
),
by_address AS (
  SELECT r.block_number, a.pool_name
  FROM raw AS r
  CROSS JOIN UNNEST(${pool_addresses}) AS a
  WHERE a.address IN UNNEST(r.coinbase_addresses)
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY r.block_number ORDER BY a.pool_name) = 1
)
SELECT
  r.block_number,
  r.block_hash,
  r.block_timestamp,
  r.block_date,
  r.block_month,
  r.weight,
  r.block_size,
  r.transaction_count,
  COALESCE(t.pool_name, a.pool_name, 'Unknown') AS pool_name,
  CASE
    WHEN t.pool_name IS NOT NULL THEN 'coinbase_tag'
    WHEN a.pool_name IS NOT NULL THEN 'payout_address'
    ELSE 'unattributed'
  END AS pool_source,
  CAST(NULL AS FLOAT64) AS floor_fee_rate,      -- step 06
  CAST(NULL AS FLOAT64) AS p05_fee_rate,        -- step 06
  CAST(NULL AS FLOAT64) AS median_fee_rate,     -- step 06
  CAST(NULL AS BOOL)    AS weight_full,         -- step 07
  CAST(NULL AS INT64)   AS full_neighbours,     -- step 07
  CAST(NULL AS BOOL)    AS is_full              -- step 07
FROM raw AS r
LEFT JOIN by_tag AS t USING (block_number)
LEFT JOIN by_address AS a USING (block_number)
