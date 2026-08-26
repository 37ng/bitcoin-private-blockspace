-- Step 04a: the working set for the union-find pass.
--
-- Only transactions with a relative in their own block can change price by
-- grouping. Every other transaction is a package of one and already carries
-- its effective rate from step 03, so it never has to travel to Python.
CREATE OR REPLACE TABLE `${dst}.tx_in_package`
PARTITION BY block_month
CLUSTER BY block_number
OPTIONS (description = "Transactions with a CPFP relative in the same block.")
AS
SELECT tx_hash, block_number, block_month, fee, virtual_size, parent_hashes
FROM `${dst}.txs`
WHERE in_package AND NOT is_coinbase
