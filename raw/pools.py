import json
import os

_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "pools_known.json")


MISSING = (f"{os.path.basename(_JSON_PATH)} is missing or empty. "
           "Run `python refresh_pools.py` to download the pool list.")


def load_pools():
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
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def tag_struct_sql() -> str:
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
    ids = load_pool_ids()
    if not ids:
        # An empty array literal needs an explicit type in BigQuery.
        return "ARRAY<STRUCT<pool_name STRING, pool_unique_id INT64>>[]"
    rows = [f"STRUCT({_sql_string(name)} AS pool_name, "
            f"{pool_id} AS pool_unique_id)"
            for pool_id, name in sorted(ids.items())]
    return "[\n    " + ",\n    ".join(rows) + "\n  ]"
