"""Offline tests for the pool-list refresh.

The upstream file has changed shape once already: an object keyed by pool
name became a list of objects. That break stopped the script dead, so both
shapes are exercised here with literal payloads and no network.

The other thing under test is the pool id. It is mempool.space's
`poolUniqueId`, the only id that matches the acceleration data, and it is
what turns the `pools` array of an acceleration into pool names.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pools
import refresh_pools as rp

# The list form, as served today.
LIST_PAYLOAD = [
    {"id": 111, "name": "Foundry USA", "addresses": ["bc1qfoundry"],
     "tags": ["/Foundry USA Pool"], "link": "https://foundrydigital.com"},
    {"id": 44, "name": "AntPool", "addresses": [], "tags": ["/AntPool/"]},
]

# The older object form, kept working on purpose.
DICT_PAYLOAD = {
    "Foundry USA": {"addresses": ["bc1qfoundry"], "tags": ["/Foundry USA Pool"]},
    "AntPool": {"addresses": [], "tags": ["/AntPool/"]},
}


# --- reading the upstream file ------------------------------------------

def test_the_list_form_is_read():
    out = rp.convert(LIST_PAYLOAD)
    assert set(out) == {"Foundry USA", "AntPool"}
    assert out["Foundry USA"]["tags"] == ["/Foundry USA Pool"]
    assert out["Foundry USA"]["addresses"] == ["bc1qfoundry"]


def test_the_old_object_form_still_works():
    assert rp.convert(DICT_PAYLOAD) == {
        "Foundry USA": {"tags": ["/Foundry USA Pool"],
                        "addresses": ["bc1qfoundry"]},
        "AntPool": {"tags": ["/AntPool/"], "addresses": []},
    }


def test_both_forms_agree_apart_from_the_id():
    from_list = {name: {"tags": e["tags"], "addresses": e["addresses"]}
                 for name, e in rp.convert(LIST_PAYLOAD).items()}
    assert from_list == rp.convert(DICT_PAYLOAD)


def test_a_pool_with_neither_tag_nor_address_is_dropped():
    payload = LIST_PAYLOAD + [{"id": 9, "name": "Ghost", "tags": [],
                               "addresses": []}]
    assert "Ghost" not in rp.convert(payload)


def test_junk_entries_are_skipped_not_fatal():
    payload = ["nonsense", None, {"id": 5}, {"name": ""}] + LIST_PAYLOAD
    assert set(rp.convert(payload)) == {"Foundry USA", "AntPool"}


def test_missing_lists_are_treated_as_empty():
    out = rp.convert([{"id": 7, "name": "Tagless", "addresses": ["bc1q7"]}])
    assert out["Tagless"] == {"tags": [], "addresses": ["bc1q7"], "id": 7}


def test_an_unexpected_shape_returns_nothing_so_main_refuses_to_write():
    assert rp.convert("not a pool list") == {}


# --- the pool id --------------------------------------------------------

def test_the_pool_unique_id_is_kept():
    out = rp.convert(LIST_PAYLOAD)
    assert out["Foundry USA"]["id"] == 111
    assert out["AntPool"]["id"] == 44


def test_an_entry_without_an_id_carries_none():
    out = rp.convert([{"name": "Nameless Id", "tags": ["/x/"]}])
    assert "id" not in out["Nameless Id"]


def _write_known(tmp_path, monkeypatch, payload):
    path = tmp_path / "pools_known.json"
    path.write_text(json.dumps(rp.convert(payload)))
    monkeypatch.setattr(pools, "_JSON_PATH", str(path))


def test_the_id_map_reads_back_from_the_written_file(tmp_path, monkeypatch):
    _write_known(tmp_path, monkeypatch, LIST_PAYLOAD)
    assert pools.load_pool_ids() == {111: "Foundry USA", 44: "AntPool"}


def test_the_id_map_is_empty_without_a_refreshed_file(tmp_path, monkeypatch):
    monkeypatch.setattr(pools, "_JSON_PATH", str(tmp_path / "absent.json"))
    assert pools.load_pool_ids() == {}
    assert pools.pool_id_struct_sql().startswith("ARRAY<STRUCT<")


def test_the_id_map_becomes_a_sql_lookup(tmp_path, monkeypatch):
    _write_known(tmp_path, monkeypatch, LIST_PAYLOAD)
    sql = pools.pool_id_struct_sql()
    assert "STRUCT('Foundry USA' AS pool_name, 111 AS pool_unique_id)" in sql
    assert "STRUCT('AntPool' AS pool_name, 44 AS pool_unique_id)" in sql


def test_writing_the_id_map_leaves_coinbase_attribution_alone(tmp_path,
                                                              monkeypatch):
    """The id decodes the offer array only; the coinbase still names the pool."""
    _write_known(tmp_path, monkeypatch, LIST_PAYLOAD)
    assert pools.load_pools() == [
        ("AntPool", ["/AntPool/"], []),
        ("Foundry USA", ["/Foundry USA Pool"], ["bc1qfoundry"]),
    ]
