import json
from collections import defaultdict
from pathlib import Path
from typing import TypeAlias, TypedDict, cast

import requests

POOL_URL = "https://raw.githubusercontent.com/mempool/mining-pools/master/pools-v2.json"
DATA_DIR = Path(__file__).parent / "data"
SITE_DATA_DIR = (Path(__file__).parents[2] / "37ng.github.io" / "src" /
                 "posts" / "bitcoin" / "data")
MONTH_OUTPUT_PATH = SITE_DATA_DIR / "mempool-space-by-month.json"
POOL_OUTPUT_PATH = SITE_DATA_DIR / "mempool-space-by-pool.json"
HEADERS = {"User-Agent": "bitcoin-private-blockspace/1.0 (research)"}
TIMEOUT = 30


class PoolEntry(TypedDict, total=False):
    id: int
    name: str


PoolNamesResponse: TypeAlias = dict[str, PoolEntry] | list[PoolEntry]


class MonthlyFeeDeltas(TypedDict):
    pools: dict[str, int]
    sum: int


class PoolFeeDeltas(TypedDict):
    months: dict[str, int]
    sum: int


def fetch_pool_names(url: str = POOL_URL) -> dict[int, str]:
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    payload = cast(PoolNamesResponse, response.json())
    if isinstance(payload, dict):
        entries = [dict(entry, name=entry.get("name") or name)
                   for name, entry in payload.items()
                   if isinstance(entry, dict)]
    elif isinstance(payload, list):
        entries = payload
    else:
        raise ValueError("pool list must be an object or array")
    pool_names = {}
    for entry in entries:
        pool_id = entry.get("id")
        pool_name = entry.get("name")
        if (isinstance(pool_id, int) and not isinstance(pool_id, bool)
                and isinstance(pool_name, str)):
            pool_names[pool_id] = pool_name
    return pool_names


def aggregate_pool_fees(
        data_dir: Path,
        pool_names: dict[int, str],
) -> tuple[dict[str, MonthlyFeeDeltas], dict[str, PoolFeeDeltas]]:
    monthly_totals: dict[str, defaultdict[str, int]] = {}
    for path in sorted(data_dir.glob("*.json")):
        month = path.stem
        monthly_totals[month] = defaultdict(int)
        content = path.read_text()
        if not content.strip():
            continue
        records = json.loads(content)
        for record in records:
            if "completed" not in str(record.get("status", "")):
                continue
            pool_name = pool_names.get(record.get("minedByPoolUniqueId"))
            if pool_name is None:
                continue
            fee_delta = record.get("feeDelta") or 0
            monthly_totals[month][pool_name] += fee_delta

    by_month: dict[str, MonthlyFeeDeltas] = {}
    pool_totals: defaultdict[str, int] = defaultdict(int)
    for month, totals in monthly_totals.items():
        pools = dict(sorted(totals.items(), key=lambda item: item[1],
                            reverse=True))
        by_month[month] = {"pools": pools, "sum": sum(pools.values())}
        for pool_name, fee_delta in pools.items():
            pool_totals[pool_name] += fee_delta

    by_pool: dict[str, PoolFeeDeltas] = {}
    for pool_name, total in sorted(pool_totals.items(),
                                   key=lambda item: item[1], reverse=True):
        by_pool[pool_name] = {
            "months": {month: totals.get(pool_name, 0)
                       for month, totals in monthly_totals.items()},
            "sum": total,
        }
    return by_month, by_pool


def run(data_dir: Path = DATA_DIR, pool_url: str = POOL_URL,
        month_output_path: Path = MONTH_OUTPUT_PATH,
        pool_output_path: Path = POOL_OUTPUT_PATH) -> None:
    by_month, by_pool = aggregate_pool_fees(data_dir,
                                            fetch_pool_names(pool_url))
    month_output_path.write_text(json.dumps(by_month, indent=2) + "\n")
    pool_output_path.write_text(json.dumps(by_pool, indent=2) + "\n")
    print(f"wrote {month_output_path}")
    print(f"wrote {pool_output_path}")