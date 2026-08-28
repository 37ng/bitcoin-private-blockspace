-- Step 04d: the ancestor-limit reason, the one relay rule that needs the CPFP
-- graph rather than the transaction alone.
--
-- A mempool caps how many unconfirmed ancestors a transaction may have, and
-- how many vbytes they add up to. Every transaction in one block was
-- unconfirmed at the same moment, so a transaction with more in-block
-- ancestors than the limit allows is a transaction a default node would have
-- refused. The transaction over the limit is the one refused, which is why
-- this counts ancestors and not descendants: a parent with 40 children in one
-- block broke the descendant limit too, but nothing in the data says which
-- child was the one too many, and an ambiguous case is not counted.
--
--   before ${cluster_mempool_date}   more than ${ancestor_limit} in-block
--                                    ancestors, itself included, or more than
--                                    ${ancestor_size_limit_vb} vB of them
--   from ${cluster_mempool_date}     Core 31 dropped the ancestor and
--                                    descendant limits for a cluster limit,
--                                    which is a property of the whole
--                                    connected component and so names no
--                                    single transaction. Nothing is counted.
--   from ${truc_standard_date}       a version 3 transaction may have
--                                    ${truc_ancestor_limit} ancestors,
--                                    itself included
--
-- This is the only step that adds to `is_nonrelayable` after step 03 built
-- it. It ORs one more term in and never restates the others, so the list of
-- reasons still lives in exactly one place.
UPDATE `${dst}.txs` AS t
SET
  nonrelay_ancestor_limit = f.nonrelay_ancestor_limit,
  is_nonrelayable = t.is_nonrelayable OR f.nonrelay_ancestor_limit
FROM (
  SELECT
    p.tx_hash,
    p.block_number,
    COALESCE(NOT x.is_coinbase AND (
        (x.block_timestamp < TIMESTAMP('${cluster_mempool_date}')
         AND (p.ancestor_count > ${ancestor_limit}
              OR p.ancestor_vsize > ${ancestor_size_limit_vb}))
     OR (x.block_timestamp >= TIMESTAMP('${truc_standard_date}')
         AND x.version = ${truc_version}
         AND p.ancestor_count > ${truc_ancestor_limit})
    ), FALSE) AS nonrelay_ancestor_limit
  FROM `${dst}.tx_packages` AS p
  JOIN `${dst}.txs` AS x USING (tx_hash, block_number)
) AS f
WHERE t.tx_hash = f.tx_hash AND t.block_number = f.block_number
