"""Fixture blocks in a local SQLite database.

The union-find step is the one place where the pipeline leaves SQL, so it is
the one place that needs its own tests. A local database standing in for
BigQuery lets the real reader and the real algorithm run with no credentials
and no cost.

Each fixture block holds one CPFP shape with hand-computable arithmetic:

  block 100  a 3-deep chain               a <- b <- c
  block 101  one parent, three children   p <- c1, c2, c3
  block 102  two singletons               no edges at all
  block 103  two disjoint chains          a <- b   and   x <- y
  block 104  one child, two parents       p1, p2 <- c
  block 105  a parent outside the block   the outside hash must be ignored
"""

import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "utils"))

# tx_hash, block, fee (sat), vsize (vB), parents
FIXTURE_TXS = [
    # 100: chain. package fee 100+500+600=1200 over 300 vB -> 4.0 sat/vB
    ("a", 100, 100, 100, []),
    ("b", 100, 500, 100, ["a"]),
    ("c", 100, 600, 100, ["b"]),

    # 101: fan. 20+300+400+480=1200 over 400 vB -> 3.0 sat/vB
    ("p", 101, 20, 100, []),
    ("c1", 101, 300, 100, ["p"]),
    ("c2", 101, 400, 100, ["p"]),
    ("c3", 101, 480, 100, ["p"]),

    # 102: no edges. each keeps its own rate: 5.0 and 2.5
    ("s1", 102, 500, 100, []),
    ("s2", 102, 500, 200, []),

    # 103: two chains that must not merge. 900/300=3.0 and 200/200=1.0
    ("d_a", 103, 300, 200, []),
    ("d_b", 103, 600, 100, ["d_a"]),
    ("d_x", 103, 50, 100, []),
    ("d_y", 103, 150, 100, ["d_x"]),

    # 104: one child funding two parents. 10+10+880=900 over 300 -> 3.0
    ("m_p1", 104, 10, 100, []),
    ("m_p2", 104, 10, 100, []),
    ("m_c", 104, 880, 100, ["m_p1", "m_p2"]),

    # 105: the parent confirmed in an earlier block, so no grouping.
    # o_child keeps 700/100 = 7.0
    ("o_child", 105, 700, 100, ["not_in_this_block"]),
]


@pytest.fixture(scope="session")
def fixture_db(tmp_path_factory):
    """A local database shaped like `tx_in_package`."""
    path = tmp_path_factory.mktemp("blockspace") / "fixture.db"
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE tx_in_package (
            tx_hash TEXT,
            block_number INTEGER,
            fee INTEGER,
            virtual_size INTEGER,
            parent_hashes TEXT
        )
    """)
    conn.executemany(
        "INSERT INTO tx_in_package VALUES (?, ?, ?, ?, ?)",
        [(h, b, f, v, json.dumps(p)) for h, b, f, v, p in FIXTURE_TXS])
    conn.commit()
    conn.close()
    return str(path)
