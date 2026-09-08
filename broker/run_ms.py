import json
from collections import defaultdict
from pathlib import Path

import requests

POOL_URL = "https://raw.githubusercontent.com/mempool/mining-pools/master/pools-v2.json"
DATA_DIR = Path(__file__).parent / "data"
HEADERS = {"User-Agent": "bitcoin-private-blockspace/1.0 (research)"}
TIMEOUT = 30


def fetch_pool_names(url: str = POOL_URL) -> dict[int, str]:
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        entries = [dict(entry, name=entry.get("name") or name)
                   for name, entry in payload.items()
                   if isinstance(entry, dict)]
    elif isinstance(payload, list):
        entries = payload
    else:
        raise ValueError("pool list must be an object or array")
    return {entry["id"]: entry["name"] for entry in entries
            if isinstance(entry.get("id"), int)
            and not isinstance(entry.get("id"), bool)
            and isinstance(entry.get("name"), str)}


def aggregate_pool_fees(data_dir: Path, pool_names: dict[int, str]) -> dict[str, dict[str, int]]:
    totals = defaultdict(lambda: {"effectiveFee": 0, "feeDelta": 0,
                                  "totalTxFee": 0})
    for path in sorted(data_dir.glob("*.json")):
        content = path.read_text()
        if not content.strip():
            continue
        records = json.loads(content)
        for record in records:
            if not str(record.get("status", "")).startswith("completed"):
                continue
            pool_name = pool_names.get(record.get("minedByPoolUniqueId"))
            if pool_name is None:
                continue
            effective_fee = record.get("effectiveFee") or 0
            fee_delta = record.get("feeDelta") or 0
            totals[pool_name]["effectiveFee"] += effective_fee
            totals[pool_name]["feeDelta"] += fee_delta
            totals[pool_name]["totalTxFee"] += effective_fee + fee_delta
    return dict(totals)


def run(data_dir: Path = DATA_DIR, pool_url: str = POOL_URL) -> None:
    totals = aggregate_pool_fees(data_dir, fetch_pool_names(pool_url))
    ordered = dict(sorted(totals.items(),
                          key=lambda item: item[1]["totalTxFee"],
                          reverse=True))
    print(json.dumps(ordered, indent=2))