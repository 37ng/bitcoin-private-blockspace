"""Union-find over the in-block CPFP graph.

A child that pays for its parent means the two moved at one price, so the
fee rate belongs to the package, not to either transaction. The graph is
undirected: any set of transactions joined by parent/child edges inside one
block collapses to a single package, whatever the shape — a chain, a fan of
children on one parent, or several parents funded by one child. A transaction
with no in-block relative is a package of one.

The same pass also walks the graph in its directed form, upwards, to count
each transaction's in-block ancestors. Mempool policy caps how many
unconfirmed ancestors a transaction may have, and transactions confirmed in
one block were all unconfirmed at the same moment, so an over-long ancestor
chain in a block is a chain no default node would have relayed. Step 04d
turns the counts into a non-relayable reason.

Pure Python, no BigQuery import, so the fixture tests run offline.
"""

from collections import defaultdict

# Ancestor counting stops here. Every rule that reads the count only asks
# whether it is over a limit, and the largest limit is 25, so a walk that has
# already found 26 distinct ancestors has answered every question that will be
# put to it. Without the stop, one block-wide CPFP graph would be walked once
# per member.
ANCESTOR_CAP = 26


class UnionFind:
    """Disjoint-set over hashable items, with path compression by rank."""

    def __init__(self, items=()):
        self._parent = {}
        self._rank = {}
        for item in items:
            self.add(item)

    def add(self, item):
        if item not in self._parent:
            self._parent[item] = item
            self._rank[item] = 0
        return item

    def find(self, item):
        self.add(item)
        # Iterative, so a long CPFP chain cannot exhaust the stack.
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return ra
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1
        return ra

    def groups(self):
        """{root: [members]} for every item seen."""
        out = defaultdict(list)
        for item in self._parent:
            out[self.find(item)].append(item)
        return dict(out)


def package_transactions(txs, ancestor_cap=ANCESTOR_CAP):
    """Group one block's transactions into CPFP packages.

    `txs` is an iterable of objects or mappings with `tx_hash`, `fee`,
    `virtual_size` and `parent_hashes`. Parent hashes that are not in this
    block are ignored — a parent confirmed in an earlier block was bought
    separately and shares no price with its child.

    Returns a list of dicts, one per input transaction:
        tx_hash, package_id, package_tx_count, package_fee, package_vsize,
        effective_fee_rate, ancestor_count, ancestor_vsize

    `package_id` is the smallest member hash, so the same block always yields
    the same ids whatever order the rows arrive in.
    """
    rows = [_as_row(t) for t in txs]
    by_hash = {r["tx_hash"]: r for r in rows}
    ancestors = ancestor_stats(by_hash, ancestor_cap)

    uf = UnionFind(by_hash)
    for row in rows:
        for parent in row["parent_hashes"] or ():
            if parent in by_hash:      # same-block parent only
                uf.union(row["tx_hash"], parent)

    out = []
    for members in uf.groups().values():
        package_id = min(members)
        fee = sum(by_hash[h]["fee"] for h in members)
        vsize = sum(by_hash[h]["virtual_size"] for h in members)
        rate = (fee / vsize) if vsize else None
        for tx_hash in members:
            count, ancestor_vsize = ancestors[tx_hash]
            out.append({
                "tx_hash": tx_hash,
                "package_id": package_id,
                "package_tx_count": len(members),
                "package_fee": fee,
                "package_vsize": vsize,
                "effective_fee_rate": rate,
                "ancestor_count": count,
                "ancestor_vsize": ancestor_vsize,
            })
    out.sort(key=lambda r: (r["package_id"], r["tx_hash"]))
    return out


def ancestor_stats(by_hash, cap=ANCESTOR_CAP):
    """{tx_hash: (ancestor_count, ancestor_vsize)} over one block.

    Both figures count the transaction itself, which is what the mempool
    limits count. Only parents confirmed in the same block are followed: a
    parent already confirmed does not count against an unconfirmed-ancestor
    limit.

    The walk stops at `cap` distinct ancestors, so a count that reaches the
    cap means "at least this many" and the vbyte sum that comes with it is
    partial. Every rule reading these is a "more than" test with a limit below
    the cap, so a capped answer decides it either way.
    """
    stats = {}
    for tx_hash, row in by_hash.items():
        seen = {tx_hash}
        vsize = row["virtual_size"]
        frontier = [tx_hash]
        while frontier and len(seen) < cap:
            parents = []
            for member in frontier:
                for parent in by_hash[member]["parent_hashes"]:
                    if parent not in by_hash or parent in seen:
                        continue           # same-block parents only
                    seen.add(parent)
                    vsize += by_hash[parent]["virtual_size"]
                    parents.append(parent)
                    if len(seen) >= cap:
                        break
                if len(seen) >= cap:
                    break
            frontier = parents
        stats[tx_hash] = (len(seen), vsize)
    return stats


def _as_row(tx):
    """Accept dicts, BigQuery Rows, sqlite3.Row, or plain objects."""
    if isinstance(tx, dict):
        get = tx.get
    elif hasattr(tx, "keys"):          # sqlite3.Row, bigquery.Row
        keys = set(tx.keys())
        get = lambda k, d=None: tx[k] if k in keys else d  # noqa: E731
    else:
        get = lambda k, d=None: getattr(tx, k, d)          # noqa: E731

    parents = get("parent_hashes") or []
    if isinstance(parents, str):       # sqlite stores the list as JSON text
        import json
        parents = json.loads(parents) if parents.strip() else []
    return {
        "tx_hash": get("tx_hash"),
        "fee": int(get("fee") or 0),
        "virtual_size": int(get("virtual_size") or 0),
        "parent_hashes": list(parents),
    }
