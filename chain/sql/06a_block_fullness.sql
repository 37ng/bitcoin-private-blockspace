-- Step 06a: is the block full?
--
-- A discount only means something when the space was scarce. In a block with
-- room to spare, a cheap transaction costs no one anything — the miner had
-- nothing else to put there. Two conditions must hold:
--
--   the block is at or above ${block_weight_full} WU of the 4,000,000 limit
--   (a block can rarely be filled to the last unit, so the test has slack), and
--
--   at least ${full_neighbours_required} of its 6 neighbours are also that
--   full, which is what separates sustained demand from one busy minute.
UPDATE `${dst}.blocks` AS b
SET
  weight_full = f.weight_full,
  full_neighbours = f.full_neighbours,
  is_full = f.weight_full AND f.full_neighbours >= ${full_neighbours_required}
FROM (
  SELECT
    centre.block_number,
    centre.weight >= ${block_weight_full} AS weight_full,
    COUNTIF(neighbour.weight >= ${block_weight_full}) AS full_neighbours
  FROM `${dst}.blocks` AS centre
  CROSS JOIN UNNEST([${neighbour_offsets}]) AS nb_offset
  LEFT JOIN `${dst}.blocks` AS neighbour
    ON neighbour.block_number = centre.block_number + nb_offset
  GROUP BY centre.block_number, centre.weight
) AS f
WHERE b.block_number = f.block_number
