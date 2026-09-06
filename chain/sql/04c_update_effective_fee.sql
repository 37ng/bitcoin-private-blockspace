-- Step 04c: write the package rates back onto the transaction table.
--
-- `tx_packages` is produced by `effective_fee.py`, which reads each block into
-- Python, unions the CPFP edges, and prices every package as
-- sum(fee) / sum(vbytes).
UPDATE `${dst}.txs` AS t
SET
  effective_fee_rate = p.effective_fee_rate,
  package_id = p.package_id,
  package_tx_count = p.package_tx_count
FROM `${dst}.tx_packages` AS p
WHERE t.tx_hash = p.tx_hash
  AND t.block_number = p.block_number
