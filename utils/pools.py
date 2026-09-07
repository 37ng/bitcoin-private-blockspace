import json
import os

JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "pools.json")


MISSING = (f"{os.path.basename(JSON_PATH)} is missing or empty. "
           "Run `python refresh_pools.py` to download the pool list.")


Pool = tuple[str, list[str], list[str]]


def _load() -> dict[str, dict]:
    if not os.path.exists(JSON_PATH):
        raise RuntimeError(MISSING)
    with open(JSON_PATH) as fh:
        return json.load(fh)


def load_pools() -> list[Pool]:
    pools: list[Pool] = [
        (name, entry.get("tags") or [], entry.get("addresses") or [])
        for name, entry in sorted(_load().items())]
    if not pools:
        raise RuntimeError(MISSING)
    return pools


def load_pool_ids() -> dict[int, str]:
    return {entry["id"]: name for name, entry in _load().items()
            if "id" in entry}


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def tag_struct_sql() -> str:
    rows: list[str] = []
    seen: set[str] = set()
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
    pools = load_pools();
    owners: dict[str, set[str]] = {}
    for name, _tags, addrs in pools:
        for addr in addrs:
            if not addr or " " in addr:  # skip placeholders
                continue
            owners.setdefault(addr, set()).add(name)
    rows: list[str] = []
    for name, _tags, addrs in pools:
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
