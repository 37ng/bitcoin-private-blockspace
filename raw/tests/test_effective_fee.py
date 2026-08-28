"""The read path: does the pipeline pull blocks out of a database correctly?

Same code path as production, with a local database in place of BigQuery.
"""

import effective_fee
from tests.conftest import FIXTURE_TXS


def run_fixture(db_path, chunk_blocks=100):
    source = effective_fee.SqliteSource(db_path)
    writer = effective_fee.ListWriter()
    effective_fee.run(source, writer, chunk_blocks=chunk_blocks, verbose=False)
    return {r["tx_hash"]: r for r in writer.rows}


def test_reader_sees_every_fixture_transaction(fixture_db):
    out = run_fixture(fixture_db)
    assert set(out) == {t[0] for t in FIXTURE_TXS}


def test_block_range_covers_the_fixture(fixture_db):
    assert effective_fee.SqliteSource(fixture_db).block_range() == (100, 105)


def test_rates_match_the_hand_computed_values(fixture_db):
    out = run_fixture(fixture_db)
    expected = {
        "a": 4.0, "b": 4.0, "c": 4.0,                  # chain
        "p": 3.0, "c1": 3.0, "c2": 3.0, "c3": 3.0,     # fan
        "s1": 5.0, "s2": 2.5,                          # singletons
        "d_a": 3.0, "d_b": 3.0, "d_x": 1.0, "d_y": 1.0,  # disjoint chains
        "m_p1": 3.0, "m_p2": 3.0, "m_c": 3.0,          # two parents
        "o_child": 7.0,                                # parent outside block
    }
    for tx_hash, rate in expected.items():
        assert out[tx_hash]["effective_fee_rate"] == rate, tx_hash


def test_packages_stay_inside_their_own_block(fixture_db):
    out = run_fixture(fixture_db)
    for row in out.values():
        assert row["block_number"] in range(100, 106)
    assert out["a"]["package_id"] != out["d_a"]["package_id"]


def test_chunk_size_does_not_change_the_result(fixture_db):
    assert run_fixture(fixture_db, chunk_blocks=1) == \
           run_fixture(fixture_db, chunk_blocks=1000)


def test_grouping_splits_on_block_boundaries():
    rows = [
        {"block_number": 1, "tx_hash": "a", "fee": 1, "virtual_size": 1,
         "parent_hashes": []},
        {"block_number": 1, "tx_hash": "b", "fee": 1, "virtual_size": 1,
         "parent_hashes": ["a"]},
        {"block_number": 2, "tx_hash": "c", "fee": 1, "virtual_size": 1,
         "parent_hashes": ["a"]},  # cross-block reference, must not group
    ]
    grouped = dict(effective_fee.group_rows_by_block(rows))
    assert len(grouped[1]) == 2 and len(grouped[2]) == 1
    staged = {r["tx_hash"]: r for r in effective_fee.package_rows(rows)}
    assert staged["c"]["package_tx_count"] == 1
    assert staged["a"]["package_tx_count"] == 2


# --- ancestors ------------------------------------------------------------
#
# Mempool policy caps a transaction's unconfirmed ancestors. Everything in one
# block was unconfirmed together, so an in-block ancestor chain longer than the
# cap is a chain no default node would have relayed. Step 04d turns these
# counts into a non-relayable reason.

def chain(length, vsize=100):
    """A CPFP chain: tx 0 at the top, each one spending the one before."""
    return [{"tx_hash": str(i), "block_number": 1, "fee": 10,
             "virtual_size": vsize,
             "parent_hashes": [str(i - 1)] if i else []}
            for i in range(length)]


def ancestors_of(rows):
    from unionfind import package_transactions
    return {r["tx_hash"]: (r["ancestor_count"], r["ancestor_vsize"])
            for r in package_transactions(rows)}


def test_a_transaction_counts_itself_as_an_ancestor():
    """The mempool limit counts the transaction itself, so this does too."""
    out = ancestors_of(chain(3))
    assert out["0"] == (1, 100)
    assert out["2"] == (3, 300)


def test_the_count_stops_at_the_cap():
    """Past the cap the answer to every rule is already settled."""
    import unionfind
    out = ancestors_of(chain(40))
    assert out[str(unionfind.ANCESTOR_CAP - 1)][0] == unionfind.ANCESTOR_CAP
    assert out["39"][0] == unionfind.ANCESTOR_CAP


def test_the_cap_sits_one_over_the_limit_the_rules_use():
    """A capped count must still decide `more than 25`, which is the rule."""
    import config
    import unionfind
    assert unionfind.ANCESTOR_CAP == config.ANCESTOR_LIMIT + 1
    out = ancestors_of(chain(30))
    assert out["24"][0] == config.ANCESTOR_LIMIT       # 25 deep: still standard
    assert out["25"][0] > config.ANCESTOR_LIMIT        # 26 deep: over the limit


def test_children_are_not_ancestors():
    """A fan of children says nothing about the parent's ancestor count."""
    rows = [{"tx_hash": "p", "block_number": 1, "fee": 1, "virtual_size": 50,
             "parent_hashes": []}]
    rows += [{"tx_hash": f"c{i}", "block_number": 1, "fee": 1,
              "virtual_size": 10, "parent_hashes": ["p"]} for i in range(40)]
    out = ancestors_of(rows)
    assert out["p"] == (1, 50)
    assert out["c0"] == (2, 60)


def test_a_parent_outside_the_block_is_not_an_ancestor():
    """It was already confirmed, so it counts against no unconfirmed limit."""
    rows = [{"tx_hash": "x", "block_number": 1, "fee": 1, "virtual_size": 100,
             "parent_hashes": ["confirmed_last_week"]}]
    assert ancestors_of(rows)["x"] == (1, 100)


def test_a_diamond_counts_each_ancestor_once():
    rows = [
        {"tx_hash": "top", "block_number": 1, "fee": 1, "virtual_size": 100,
         "parent_hashes": []},
        {"tx_hash": "l", "block_number": 1, "fee": 1, "virtual_size": 100,
         "parent_hashes": ["top"]},
        {"tx_hash": "r", "block_number": 1, "fee": 1, "virtual_size": 100,
         "parent_hashes": ["top"]},
        {"tx_hash": "bottom", "block_number": 1, "fee": 1, "virtual_size": 100,
         "parent_hashes": ["l", "r"]},
    ]
    assert ancestors_of(rows)["bottom"] == (4, 400)
