"""Step 04: effective fee rate per transaction, through CPFP union-find.

Blocks are read into Python one at a time, the in-block parent/child edges are
unioned into packages, and every member of a package takes the package price,
sum(fee) / sum(vbytes). The result lands in `tx_packages` and step 05 writes it
onto `txs`.

Only transactions with an in-block relative are read (`tx_in_package`).
A transaction with no relative is its own package and already carries
`effective_fee_rate = fee / virtual_size` from step 03; sending 400 million
such rows through Python would change no number.

The reader is behind a small interface so the fixture tests can drive the same
grouping code from a local SQLite database with no credentials.
"""

import argparse
import itertools
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "utils"))

import config
from unionfind import package_transactions

PACKAGE_SCHEMA = [
    ("tx_hash", "STRING"),
    ("block_number", "INTEGER"),
    ("package_id", "STRING"),
    ("package_tx_count", "INTEGER"),
    ("package_fee", "INTEGER"),
    ("package_vsize", "INTEGER"),
    ("effective_fee_rate", "FLOAT"),
    # In-block ancestors, the transaction included, capped by
    # `unionfind.ANCESTOR_CAP`. Step 04d reads these.
    ("ancestor_count", "INTEGER"),
    ("ancestor_vsize", "INTEGER"),
]


# --- sources --------------------------------------------------------------

class BigQuerySource:
    """Reads `tx_in_package` in block-number chunks."""

    def __init__(self, table=None):
        self.table = table or f"{config.dst()}.tx_in_package"

    def block_range(self):
        import bqio
        row = bqio.rows(
            f"SELECT MIN(block_number) AS lo, MAX(block_number) AS hi "
            f"FROM `{self.table}`")[0]
        return row["lo"], row["hi"]

    def fetch(self, lo, hi):
        """Stream the chunk; a busy month is millions of rows."""
        import bqio
        sql = (f"SELECT tx_hash, block_number, fee, virtual_size, parent_hashes "
               f"FROM `{self.table}` "
               f"WHERE block_number BETWEEN {lo} AND {hi} "
               f"ORDER BY block_number")
        return bqio.stream(sql)


class SqliteSource:
    """Reads the same shape from a local database. Used by the tests.

    Expected table `tx_in_package(tx_hash TEXT, block_number INT, fee INT,
    virtual_size INT, parent_hashes TEXT)` where `parent_hashes` is a JSON
    array of hashes.
    """

    def __init__(self, path, table="tx_in_package"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.table = table

    def block_range(self):
        cur = self.conn.execute(
            f"SELECT MIN(block_number) AS lo, MAX(block_number) AS hi "
            f"FROM {self.table}")
        row = cur.fetchone()
        return row["lo"], row["hi"]

    def fetch(self, lo, hi):
        cur = self.conn.execute(
            f"SELECT tx_hash, block_number, fee, virtual_size, parent_hashes "
            f"FROM {self.table} WHERE block_number BETWEEN ? AND ? "
            f"ORDER BY block_number", (lo, hi))
        return [dict(r) for r in cur.fetchall()]


# --- grouping -------------------------------------------------------------

def group_rows_by_block(rows):
    """Yield (block_number, [rows]) for rows ordered by block number."""
    for block_number, group in itertools.groupby(
            rows, key=lambda r: r["block_number"]):
        yield block_number, list(group)


def package_rows(rows):
    """Union-find every block in `rows`; return staging rows for `tx_packages`."""
    return list(iter_package_rows(rows))


def iter_package_rows(rows):
    """Same, one block at a time, so a long run holds only one block."""
    for block_number, block_txs in group_rows_by_block(rows):
        for pkg in package_transactions(block_txs):
            pkg["block_number"] = block_number
            yield pkg


# --- writer ---------------------------------------------------------------

class BigQueryWriter:
    """Appends staging rows through load jobs, which carry no query charge."""

    def __init__(self, table=None):
        self.table = table or f"{config.dst()}.tx_packages"
        self.first = True
        self.written = 0

    def write(self, rows):
        """Load a batch as Parquet. Load jobs carry no query charge."""
        if not rows:
            return
        import pandas as pd
        from google.cloud import bigquery
        import bqio
        schema = [bigquery.SchemaField(n, t) for n, t in PACKAGE_SCHEMA]
        job_config = bigquery.LoadJobConfig(
            schema=schema,
            write_disposition=("WRITE_TRUNCATE" if self.first else "WRITE_APPEND"),
        )
        frame = pd.DataFrame(rows, columns=[n for n, _t in PACKAGE_SCHEMA])
        job = bqio.client().load_table_from_dataframe(frame, self.table,
                                                     job_config=job_config)
        job.result()
        self.first = False
        self.written += len(rows)


class ListWriter:
    """Collects rows in memory. Used by the tests."""

    def __init__(self):
        self.rows = []
        self.written = 0

    def write(self, rows):
        self.rows.extend(rows)
        self.written += len(rows)


# --- driver ---------------------------------------------------------------

def run(source, writer, chunk_blocks=None, flush_rows=None, verbose=True):
    """Walk the block range in chunks, packaging each block.

    Rows are streamed and flushed in batches, so the memory held at any moment
    is one batch rather than one chunk of a busy month.
    """
    chunk_blocks = chunk_blocks or config.UNIONFIND_CHUNK_BLOCKS
    flush_rows = flush_rows or config.UNIONFIND_FLUSH_ROWS
    lo, hi = source.block_range()
    if lo is None:
        if verbose:
            print("    no transactions in packages; nothing to do")
        return 0

    for start in range(lo, hi + 1, chunk_blocks):
        end = min(start + chunk_blocks - 1, hi)
        batch = []
        staged_here = 0
        for row in iter_package_rows(source.fetch(start, end)):
            batch.append(row)
            if len(batch) >= flush_rows:
                writer.write(batch)
                staged_here += len(batch)
                batch = []
        if batch:
            writer.write(batch)
            staged_here += len(batch)
        if verbose:
            print(f"    blocks {start}-{end}: {staged_here} package rows",
                  flush=True)
    if verbose:
        print(f"    wrote {writer.written} package rows", flush=True)
    return writer.written


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunk-blocks", type=int,
                        default=config.UNIONFIND_CHUNK_BLOCKS)
    parser.add_argument("--sqlite", help="run against a local database instead")
    args = parser.parse_args()

    if args.sqlite:
        source, writer = SqliteSource(args.sqlite), ListWriter()
    else:
        source, writer = BigQuerySource(), BigQueryWriter()
    run(source, writer, chunk_blocks=args.chunk_blocks)


if __name__ == "__main__":
    main()
