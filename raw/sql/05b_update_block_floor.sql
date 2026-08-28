-- Step 05b: write the neighbour median onto the block.
UPDATE `${dst}.blocks` AS b
SET
  floor_fee_rate = n.floor_fee_rate,
  p05_fee_rate = p.p05_fee_rate,
  median_fee_rate = p.median_fee_rate
FROM (
  SELECT
    block_number,
    -- six neighbour values, or nothing
    IF(ARRAY_LENGTH(sorted) = 6,
       (sorted[OFFSET(2)] + sorted[OFFSET(3)]) / 2,
       NULL) AS floor_fee_rate
  FROM (
    SELECT
      centre.block_number,
      ARRAY_AGG(neighbour.p05_fee_rate IGNORE NULLS
                ORDER BY neighbour.p05_fee_rate) AS sorted
    FROM `${dst}.block_percentiles` AS centre
    CROSS JOIN UNNEST([${neighbour_offsets}]) AS nb_offset
    LEFT JOIN `${dst}.block_percentiles` AS neighbour
      ON neighbour.block_number = centre.block_number + nb_offset
    GROUP BY centre.block_number
  )
) AS n
LEFT JOIN `${dst}.block_percentiles` AS p ON p.block_number = n.block_number
WHERE b.block_number = n.block_number
