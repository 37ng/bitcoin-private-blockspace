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
