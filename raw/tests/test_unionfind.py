import pytest

from unionfind import UnionFind, package_transactions


def tx(tx_hash, fee, vsize, parents=()):
    return {"tx_hash": tx_hash, "fee": fee, "virtual_size": vsize,
            "parent_hashes": list(parents)}


def by_hash(rows):
    return {r["tx_hash"]: r for r in rows}


def test_singleton_keeps_its_own_rate():
    out = by_hash(package_transactions([tx("a", 500, 100)]))
    assert out["a"]["package_tx_count"] == 1
    assert out["a"]["effective_fee_rate"] == 5.0
    assert out["a"]["package_id"] == "a"


def test_chain_of_three_collapses_to_one_package():
    rows = [tx("a", 100, 100), tx("b", 500, 100, ["a"]), tx("c", 600, 100, ["b"])]
    out = by_hash(package_transactions(rows))
    assert {r["package_id"] for r in out.values()} == {"a"}
    assert all(r["package_tx_count"] == 3 for r in out.values())
    # (100 + 500 + 600) / 300
    assert all(r["effective_fee_rate"] == 4.0 for r in out.values())


def test_fan_of_children_on_one_parent():
    rows = [tx("p", 20, 100), tx("c1", 300, 100, ["p"]),
            tx("c2", 400, 100, ["p"]), tx("c3", 480, 100, ["p"])]
    out = by_hash(package_transactions(rows))
    assert all(r["package_tx_count"] == 4 for r in out.values())
    assert out["p"]["effective_fee_rate"] == 3.0  # 1200 / 400


def test_one_child_funding_two_parents():
    rows = [tx("p1", 10, 100), tx("p2", 10, 100), tx("c", 880, 100, ["p1", "p2"])]
    out = by_hash(package_transactions(rows))
    assert all(r["package_tx_count"] == 3 for r in out.values())
    assert out["c"]["effective_fee_rate"] == 3.0  # 900 / 300


def test_disjoint_chains_do_not_merge():
    rows = [tx("a", 300, 200), tx("b", 600, 100, ["a"]),
            tx("x", 50, 100), tx("y", 150, 100, ["x"])]
    out = by_hash(package_transactions(rows))
    assert out["a"]["package_id"] == out["b"]["package_id"] == "a"
    assert out["x"]["package_id"] == out["y"]["package_id"] == "x"
    assert out["a"]["effective_fee_rate"] == 3.0   # 900 / 300
    assert out["x"]["effective_fee_rate"] == 1.0   # 200 / 200


def test_parent_outside_the_block_is_ignored():
    rows = [tx("child", 700, 100, ["confirmed_last_week"])]
    out = by_hash(package_transactions(rows))
    assert out["child"]["package_tx_count"] == 1
    assert out["child"]["effective_fee_rate"] == 7.0


def test_package_id_and_rate_do_not_depend_on_row_order():
    rows = [tx("a", 100, 100), tx("b", 500, 100, ["a"]), tx("c", 600, 100, ["b"])]
    forward = package_transactions(rows)
    backward = package_transactions(list(reversed(rows)))
    assert forward == backward


def test_a_cheap_parent_with_a_paying_child_is_priced_as_one():
    rows = [tx("parent", 20, 100), tx("child", 980, 100, ["parent"])]
    out = by_hash(package_transactions(rows))
    assert out["parent"]["effective_fee_rate"] == 5.0
    assert out["child"]["effective_fee_rate"] == 5.0


def test_long_chain_does_not_exhaust_the_stack():
    rows = [tx("t0", 100, 100)]
    for i in range(1, 5000):
        rows.append(tx(f"t{i}", 100, 100, [f"t{i - 1}"]))
    out = package_transactions(rows)
    assert len({r["package_id"] for r in out}) == 1
    assert out[0]["package_tx_count"] == 5000


def test_zero_vsize_yields_no_rate_rather_than_a_crash():
    out = by_hash(package_transactions([tx("a", 100, 0)]))
    assert out["a"]["effective_fee_rate"] is None


def test_union_find_basics():
    uf = UnionFind("abc")
    uf.union("a", "b")
    assert uf.find("a") == uf.find("b")
    assert uf.find("c") != uf.find("a")
    uf.union("b", "c")
    assert len(uf.groups()) == 1
    uf.union("a", "c")  # already joined; must stay stable
    assert len(uf.groups()) == 1


@pytest.mark.parametrize("parents", [None, [], ()])
def test_missing_parent_list_is_accepted(parents):
    out = package_transactions(
        [{"tx_hash": "a", "fee": 100, "virtual_size": 100,
          "parent_hashes": parents}])
    assert out[0]["package_tx_count"] == 1
