# bitcoin private blockspace

how much Bitcoin block space between Jan 2023 and now was sold outside the public fee auction?

We will have 3 type of market, plausibility high to low
1. data from mempool.space /acceleration api, which txs they publish as private tx fee.
2. non-relayable txs, which is obvious that they are private.
3. effective tx fee < fraction of block floor fee, then we consider it private fee tx.

## data pipeline

Use data in google cloud big query public dataset: `crypto_bitcoin`, in which we have `blocks` and `transactions` tables.

Don't partition by month or cluster by block number when anticipated table is less than 1GB.

### blocks

fetch from `blocks` table, this table has single purpose of associating block to its miner pool, add a `floor_fee_rate` column.

### transactions

1. create txs table: collects txs from Jan 2023 to now. Need to get unspent tx hash to compute cpfp package later. add bool flags columns(bare multisig, script size)

2. flag txs table through non-relayable metrics
    1. bare multisig > 3
    2. oversized OP_RETURN: before 2025-10-8, 83 bytes, after 2025-10-8 100k bytes
    3. flag txs with less than 1 sat/vB pre-2025-sep-4 Core29.1 release, after that less than 0.1 sat/vB is flagged.
    4. oversized tx virtual_size > 100,000 (400k WU standard limit)

3. calculate effective tx fee and update
    1. read txs within each block into python
    2. do union find via undirected graph: 1-or-many children to 1-or-many parents txs are packaged together, within each pakcage(a singleton tx has a one-tx-package) they share one fee rate, as sum(fee) / sum(vB).
    3. update `effective tx fee` in existing table

4. calculate block floor
    1. p05 effective tx fee rate of each block
    2. for each block, median value of b-3,b-2,b-1,b+1,b+2,b+3 p05 value(avg of median two), and update the value in `blocks.floor_fee_rate` column.

5. flag low effective fee txs
    1. decide whether a block is full: use block weight and full neighbor blocks in b-3,b-2,b-1,b+1,b+2,b+3 blocks.
    2. (exclude txs flagged with non-relayable flags)in full blocks, if tx effective fee rate is lower than 0.3, 0.5, 0.7(sensitivity) of blocl floor fee rate, flag them in the blocks columns.

## validate data
- use public data to validate pool's hashrate share.
- use mempool.space to validate flagged transactions. matching rate.

## calculate data & write to files
- lower band: private revenue is (floor rate - effective rate) * vB
- high band: (p50 effective fee in block) * vB

## tests

### union find tests

Fixture tests: syndicate blocks in local db, test python read, union find algo.






