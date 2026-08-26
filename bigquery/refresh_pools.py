"""Refresh the pool tag table from the public mempool.space pool list.

`pools.py` ships a built-in table that covers 2023-2026, but pools change
their coinbase tags. This script downloads the public list and writes
`pools_known.json`, which `pools.py` prefers when it exists.

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
    """The upstream file keys pools by name with `tags` and `addresses`."""
    out = {}
    for name, entry in payload.items():
        if not isinstance(entry, dict):
            continue
        tags = [t for t in entry.get("tags", []) if isinstance(t, str) and t]
        addresses = [a for a in entry.get("addresses", [])
                     if isinstance(a, str) and a]
        if tags or addresses:
            out[name] = {"tags": tags, "addresses": addresses}
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
                         "keeping the built-in table")

    current = {name for name, _t, _a in pools.load_pools()}
    added = sorted(set(fresh) - current)
    dropped = sorted(current - set(fresh))
    print(f"{len(fresh)} pools upstream, {len(current)} in use")
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
    print("re-run sql/02_blocks.sql, then sanity_check.py")


if __name__ == "__main__":
    main()
