-- Step 08: does the answer survive its own thresholds?
--
-- The 3x3 grid crosses the three discount sensitivities with three definitions
-- of a full block. Read this before quoting any headline: a number that moves
-- by an order of magnitude across the grid is a statement about the cut-offs,
-- not about private blockspace.
CREATE OR REPLACE TABLE `${dst}.flag_a_sensitivity`
OPTIONS (description = "Flagged space across the sensitivity x fullness grid.")
AS
WITH grid AS (
  SELECT sensitivity, full_weight
  FROM UNNEST([${sensitivity_grid}]) AS sensitivity
  CROSS JOIN UNNEST([${fullness_grid}]) AS full_weight
),
block_fullness AS (
  SELECT
    g.sensitivity,
    g.full_weight,
    centre.block_number,
    centre.floor_fee_rate,
    centre.median_fee_rate,
    (centre.weight >= g.full_weight
     AND COUNTIF(neighbour.weight >= g.full_weight) >= ${full_neighbours_required})
      AS is_full
  FROM grid AS g
  CROSS JOIN `${dst}.blocks` AS centre
  CROSS JOIN UNNEST([${neighbour_offsets}]) AS nb_offset
  LEFT JOIN `${dst}.blocks` AS neighbour
    ON neighbour.block_number = centre.block_number + nb_offset
  GROUP BY g.sensitivity, g.full_weight, centre.block_number,
           centre.weight, centre.floor_fee_rate, centre.median_fee_rate
)
SELECT
  f.sensitivity,
  f.full_weight,
  COUNTIF(t.effective_fee_rate < f.sensitivity * f.floor_fee_rate) AS flagged_txs,
  SUM(IF(t.effective_fee_rate < f.sensitivity * f.floor_fee_rate,
         t.virtual_size, 0)) AS flagged_vbytes,
  SUM(IF(f.is_full, t.virtual_size, 0)) AS full_block_vbytes,
  SAFE_DIVIDE(
    SUM(IF(t.effective_fee_rate < f.sensitivity * f.floor_fee_rate, t.virtual_size, 0)),
    SUM(IF(f.is_full, t.virtual_size, 0))) AS flagged_share,
  SUM(IF(t.effective_fee_rate < f.sensitivity * f.floor_fee_rate,
         GREATEST(f.floor_fee_rate - t.effective_fee_rate, 0) * t.virtual_size,
         0)) / 1e8 AS lower_band_btc,
  SUM(IF(t.effective_fee_rate < f.sensitivity * f.floor_fee_rate,
         f.median_fee_rate * t.virtual_size, 0)) / 1e8 AS upper_band_btc
FROM block_fullness AS f
JOIN `${dst}.txs` AS t USING (block_number)
WHERE f.is_full
  AND f.floor_fee_rate IS NOT NULL
  AND NOT t.is_coinbase
  AND NOT t.is_nonrelayable
GROUP BY f.sensitivity, f.full_weight
ORDER BY f.sensitivity, f.full_weight
