-- Step 06b: Flag A — space that sold below the going rate in a full block.
--
-- A transaction is flagged when all of the following hold:
--   its block is full (step 06a), so the space had a market price;
--   the block has a neighbour-derived floor (step 05);
--   the transaction is relayable, so it could have taken part in the public
--     auction and its low price is not explained by policy alone;
--   its effective package rate is below sensitivity x the floor.
--
-- Three sensitivities are carried side by side. 0.3 is the conservative read
-- and 0.7 the loose one; a finding that only exists at 0.7 is a finding about
-- the threshold, not about the chain.
UPDATE `${dst}.txs` AS t
SET
  flag_a_30 = f.flag_a_30,
  flag_a_50 = f.flag_a_50,
  flag_a_70 = f.flag_a_70
FROM (
  SELECT
    x.tx_hash,
    x.block_number,
    COALESCE(b.is_full AND NOT x.is_nonrelayable AND NOT x.is_coinbase
             AND x.effective_fee_rate < ${sens_low} * b.floor_fee_rate, FALSE) AS flag_a_30,
    COALESCE(b.is_full AND NOT x.is_nonrelayable AND NOT x.is_coinbase
             AND x.effective_fee_rate < ${sens_mid} * b.floor_fee_rate, FALSE) AS flag_a_50,
    COALESCE(b.is_full AND NOT x.is_nonrelayable AND NOT x.is_coinbase
             AND x.effective_fee_rate < ${sens_high} * b.floor_fee_rate, FALSE) AS flag_a_70
  FROM `${dst}.txs` AS x
  JOIN `${dst}.blocks` AS b USING (block_number)
) AS f
WHERE t.tx_hash = f.tx_hash AND t.block_number = f.block_number
