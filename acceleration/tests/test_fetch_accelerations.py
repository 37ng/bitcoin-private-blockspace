import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fetch_accelerations as fa


def record(txid, added, status="completed"):
    return {"txid": txid, "added": added, "status": status}


# --- what may be stored --------------------------------------------------

def test_a_settled_record_is_stored():
    assert fa.is_settled(record("a", 100, "completed"))
    assert fa.is_settled(record("a", 100, "completed_provisional"))
    assert fa.is_settled(record("a", 100, "failed"))


def test_an_in_flight_record_is_skipped():
    assert not fa.is_settled(record("a", 100, "accelerating"))


def test_an_unknown_status_is_treated_as_in_flight():
    assert not fa.is_settled(record("a", 100, "queued_somehow"))
    assert not fa.is_settled({"txid": "a", "added": 100})


def test_collect_drops_in_flight_records():
    records, seen = [], set()
    batch = [record("a", 100), record("b", 90, "accelerating")]
    assert fa.collect(batch, records, seen) == 1
    assert [r["txid"] for r in records] == ["a"]


# --- the identity key ----------------------------------------------------

def test_one_txid_can_hold_two_real_requests():
    first, retry = record("7046aca3", 1787000324), record("7046aca3", 1787002346)
    assert len({fa.key_of(first), fa.key_of(retry)}) == 2


def test_a_record_returned_twice_collapses():
    copy, same = record("04fdd997", 1712571182), record("04fdd997", 1712571182)
    assert len({fa.key_of(copy), fa.key_of(same)}) == 1


def test_collect_keeps_one_copy_of_a_record_seen_twice():
    records, seen = [], set()
    fa.collect([record("a", 100)], records, seen)
    fa.collect([record("a", 100)], records, seen)
    assert len(records) == 1


# --- reading bounds ------------------------------------------------------

def test_parse_time_accepts_a_date_or_a_timestamp():
    assert fa.parse_time("2026-08-18") == 1787011200
    assert fa.parse_time("2026-08-18 05:03:00") == 1787029380


def test_parse_time_rejects_nonsense():
    with pytest.raises(SystemExit):
        fa.parse_time("last tuesday")


# --- the windows ---------------------------------------------------------

def test_the_windows_cover_the_whole_range():
    got = list(fa.windows(0, 10 * 86400, 3))
    assert got[0][0] == 0
    assert got[-1][1] == 10 * 86400


def test_the_windows_never_overlap():
    # `from` and `to` are both inclusive, so a shared second reads a record
    # twice.
    got = list(fa.windows(1_000_000, 1_000_000 + 30 * 86400, 7))
    for (_, end), (start, _) in zip(got, got[1:]):
        assert start == end + 1


def test_a_range_shorter_than_one_window_is_one_window():
    assert list(fa.windows(100, 200, 30)) == [(100, 200)]


def test_a_range_of_one_second_is_still_read():
    assert list(fa.windows(100, 100, 30)) == [(100, 100)]


# --- walking one window --------------------------------------------------

class Response:
    def __init__(self, total):
        self.headers = {"x-total-count": str(total)}


def fake_api(monkeypatch, added, page_length=50, status="completed"):
    added = sorted(added, reverse=True)
    pages = [added[i:i + page_length]
             for i in range(0, len(added), page_length)]
    calls = []

    def get_json(params, **kwargs):
        page = params["page"]
        calls.append(page)
        batch = pages[page - 1] if 1 <= page <= len(pages) else []
        return (Response(len(added)),
                [record(f"tx{t}", t, status) for t in batch])

    monkeypatch.setattr(fa, "get_json", get_json)
    return calls


def test_a_window_is_read_to_its_last_page(monkeypatch):
    times = [1_000_000 - 60 * i for i in range(120)]
    fake_api(monkeypatch, times)
    records, read = fa.fetch_window(0, 1_000_000, sleep=0, page_length=50)
    assert read == 120
    assert {r["added"] for r in records} == set(times)


def test_an_empty_window_costs_one_request(monkeypatch):
    calls = fake_api(monkeypatch, [])
    records, read = fa.fetch_window(0, 1_000_000, sleep=0, page_length=50)
    assert (records, read) == ([], 0)
    assert calls == [1]


def test_a_full_last_page_does_not_cost_another_request(monkeypatch):
    # 100 records fill two pages exactly; the count says to stop there.
    times = [1_000_000 - 60 * i for i in range(100)]
    calls = fake_api(monkeypatch, times)
    fa.fetch_window(0, 1_000_000, sleep=0, page_length=50)
    assert calls == [1, 2]


def test_a_window_stores_no_in_flight_records(monkeypatch):
    times = [1_000_000 - 60 * i for i in range(60)]
    fake_api(monkeypatch, times, status="accelerating")
    records, read = fa.fetch_window(0, 1_000_000, sleep=0, page_length=50)
    assert (records, read) == ([], 60)
