"""Mining pool attribution.

A block carries no pool field. Attribution uses two marks the pool leaves in
its own coinbase transaction:

  1. a tag in the coinbase scriptSig (`blocks.coinbase_param`, hex encoded);
  2. the payout address of a coinbase output.

The tag is checked first and the address second, which matches the order used
by mempool.space. `sanity_check.py` compares the resulting share per pool
against a public hashrate chart; a share off by more than ~2 points means
attribution is broken and every downstream number is suspect.

The marks themselves come from one place: `pools_known.json`, written by
`refresh_pools.py` from the public mempool.space pool list. There is no
second, hand-kept copy to drift out of sync with it. The file is committed,
so a checkout can run the pipeline and so the exact table behind a published
number is recorded in git; re-run `refresh_pools.py` to move it forward.
"""

import json
import os

_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "pools_known.json")


MISSING = (f"{os.path.basename(_JSON_PATH)} is missing or empty. "
           "Run `python refresh_pools.py` to download the pool list.")


def load_pools():
    """Return `[(name, [tags], [addresses])]` from the downloaded pool list."""
    if not os.path.exists(_JSON_PATH):
        raise RuntimeError(MISSING)
    with open(_JSON_PATH) as fh:
        data = json.load(fh)
    pools = [(name, entry.get("tags") or [], entry.get("addresses") or [])
             for name, entry in sorted(data.items())
             if isinstance(entry, dict)]
    if not pools:
        raise RuntimeError(MISSING)
    return pools


def load_pool_ids():
    """Return `{pool id: pool name}` from the refreshed file.

    The id is mempool.space's `poolUniqueId`, which is what the acceleration
    API reports: `mined_by_pool_unique_id`, and every entry of the `pools`
    array of partner pools a request was offered to. Older copies of the
    file carry no ids, so this is empty until `refresh_pools.py` has run
    since ids were added.
    """
    if not os.path.exists(_JSON_PATH):
        return {}
    with open(_JSON_PATH) as fh:
        data = json.load(fh)
    ids = {}
    for name, entry in data.items():
        if not isinstance(entry, dict):
            continue
        pool_id = entry.get("id")
        if isinstance(pool_id, int) and not isinstance(pool_id, bool):
            ids[pool_id] = name
    return ids


def _sql_string(value: str) -> str:
    """Quote a Python string as a BigQuery string literal."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def tag_struct_sql() -> str:
    """`UNNEST([...])` body mapping a coinbase tag to a pool name.

    Tags are emitted lowercased and matched against a lowercased coinbase
    text, because a hand-kept table gets the case wrong more often than two
    pools collide on a lowercased tag. Longer tags win, so `/Braiins Pool/`
    beats a bare `/slush/` substring.
    """
    rows = []
    seen = set()
    for name, tags, _addr in load_pools():
        for tag in tags:
            lowered = tag.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            rows.append(
                f"STRUCT({_sql_string(name)} AS pool_name, "
                f"{_sql_string(lowered)} AS tag)")
    if not rows:
        raise RuntimeError("no pool tags configured")
    return "[\n    " + ",\n    ".join(rows) + "\n  ]"


def address_struct_sql() -> str:
    """`UNNEST([...])` body mapping a coinbase payout address to a pool name."""
    owners = {}
    for name, _tags, addrs in load_pools():
        for addr in addrs:
            if not addr or " " in addr:  # skip placeholders
                continue
            owners.setdefault(addr, set()).add(name)
    rows = []
    for name, _tags, addrs in load_pools():
        for addr in addrs:
            if addr not in owners or len(owners[addr]) > 1:
                continue  # ambiguous address attributes nothing
            rows.append(
                f"STRUCT({_sql_string(name)} AS pool_name, "
                f"{_sql_string(addr)} AS address)")
    if not rows:
        # An empty array literal needs an explicit type in BigQuery.
        return ("ARRAY<STRUCT<pool_name STRING, address STRING>>[]")
    return "[\n    " + ",\n    ".join(rows) + "\n  ]"


def pool_id_struct_sql() -> str:
    """`UNNEST([...])` body mapping a mempool.space pool id to a pool name.

    This is for reading the acceleration `pools` array, the list of partner
    pools a request was offered to. It is not for block attribution: which
    pool mined a block is decided by the coinbase, above, so that the project
    keeps one definition of "which pool".
    """
    ids = load_pool_ids()
    if not ids:
        # An empty array literal needs an explicit type in BigQuery.
        return "ARRAY<STRUCT<pool_name STRING, pool_unique_id INT64>>[]"
    rows = [f"STRUCT({_sql_string(name)} AS pool_name, "
            f"{pool_id} AS pool_unique_id)"
            for pool_id, name in sorted(ids.items())]
    return "[\n    " + ",\n    ".join(rows) + "\n  ]"
