"""Refresh the pool tag table from the public mempool.space pool list.

Pools change their coinbase tags, so the table is downloaded rather than
kept by hand. This script writes `pools_known.json`, the one source
`pools.py` reads. Nothing attributes a block until it has run once, and the
file it writes is committed so a checkout can run the pipeline.

Each entry also keeps the upstream `id`, mempool.space's `poolUniqueId`.
That is the id the acceleration table stores in `mined_by_pool_unique_id`
and in its `pools` array of partner pools a request was offered to, so
writing it here is what lets that array be read as pool names
(`pools.load_pool_ids`, and `${pool_ids}` in SQL). Block attribution does
not use it and still comes from the coinbase.

    python refresh_pools.py            # write pools_known.json
    python refresh_pools.py --print    # show what changed, write nothing

Re-run `sql/02_blocks.sql` afterwards, then `sanity_check.py`, to see the
attribution move.
"""

import argparse
import json
import os

import requests

import pools

SOURCE = ("https://raw.githubusercontent.com/mempool/mining-pools/master/"
          "pools-v2.json")


def fetch(url):
    response = requests.get(url, timeout=30,
                            headers={"User-Agent": "private-blockspace-audit/1.0"})
    response.raise_for_status()
    return response.json()


def convert(payload):
    """Read the upstream pool list into `{name: {id, tags, addresses}}`.

    The file has changed shape once already. It used to be an object keyed by
    pool name; it is now a list of objects that each carry their own `name`.
    Both forms are read, so the next change costs one branch and not a broken
    script.

    `id` is mempool.space's `poolUniqueId`. That is the id the acceleration
    API reports, in `mined_by_pool_unique_id` and in the `pools` array of
    partner pools a request was offered to. It is *not* the `poolId` of the
    `/api/v1/mining/pools` endpoint: that endpoint returns both numbers and
    they differ for the same pool, so mixing them silently renames pools.
    """
    if isinstance(payload, dict):
        entries = [dict(entry, name=entry.get("name") or name)
                   for name, entry in payload.items()
                   if isinstance(entry, dict)]
    elif isinstance(payload, list):
        entries = [entry for entry in payload if isinstance(entry, dict)]
    else:
        return {}

    out = {}
    for entry in entries:
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        tags = [t for t in entry.get("tags") or [] if isinstance(t, str) and t]
        addresses = [a for a in entry.get("addresses") or []
                     if isinstance(a, str) and a]
        if not (tags or addresses):
            continue
        row = {"tags": tags, "addresses": addresses}
        pool_id = entry.get("id")
        if isinstance(pool_id, int) and not isinstance(pool_id, bool):
            row["id"] = pool_id
        out[name] = row
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=SOURCE)
    parser.add_argument("--print", dest="print_only", action="store_true")
    args = parser.parse_args()

    fresh = convert(fetch(args.url))
    if not fresh:
        raise SystemExit("the downloaded list held no usable pools; "
                         "leaving pools_known.json as it is")

    with_id = sum(1 for entry in fresh.values() if "id" in entry)
    try:
        current = {name for name, _t, _a in pools.load_pools()}
    except RuntimeError:
        current = set()  # the first run, with no pool list on disk yet
    added = sorted(set(fresh) - current)
    dropped = sorted(current - set(fresh))
    print(f"{len(fresh)} pools upstream, {len(current)} in use, "
          f"{with_id} with a pool id")
    if added:
        print(f"  new: {', '.join(added[:20])}"
              f"{' ...' if len(added) > 20 else ''}")
    if dropped:
        print(f"  not upstream: {', '.join(dropped[:20])}"
              f"{' ...' if len(dropped) > 20 else ''}")

    if args.print_only:
        return
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "pools_known.json")
    with open(path, "w") as fh:
        json.dump(fresh, fh, indent=1, sort_keys=True)
    print(f"wrote {path}")
    print(f"{with_id} pool ids available to read the acceleration "
          f"`pools` array")
    print("re-run sql/02_blocks.sql, then sanity_check.py")


if __name__ == "__main__":
    main()
