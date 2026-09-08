import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import main
import run_ms


def test_aggregate_pool_fees_reads_every_month(tmp_path):
    (tmp_path / "2023-12.json").write_text("")
    (tmp_path / "2024-01.json").write_text(json.dumps([
        {"status": "completed", "minedByPoolUniqueId": 44,
         "effectiveFee": 100, "feeDelta": 25},
        {"status": "failed", "minedByPoolUniqueId": 44,
         "effectiveFee": 900, "feeDelta": 900},
    ]))
    (tmp_path / "2024-02.json").write_text(json.dumps([
        {"status": "completed_provisional", "minedByPoolUniqueId": 44,
         "effectiveFee": 200, "feeDelta": 50},
           {"status": "provisionally_completed", "minedByPoolUniqueId": 44,
            "effectiveFee": 400, "feeDelta": 100},
        {"status": "completed", "minedByPoolUniqueId": 999,
         "effectiveFee": 300, "feeDelta": 75},
    ]))

    assert run_ms.aggregate_pool_fees(tmp_path, {44: "AntPool"}) == {
        "2023-12": {"pools": {}, "sum": 0},
        "2024-01": {"pools": {"AntPool": 25}, "sum": 25},
        "2024-02": {"pools": {"AntPool": 150}, "sum": 150},
    }


def test_fetch_pool_names_uses_requested_url(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"id": 111, "name": "Foundry USA"}]

    def get(url, headers, timeout):
        assert url == run_ms.POOL_URL
        assert headers == run_ms.HEADERS
        assert timeout == run_ms.TIMEOUT
        return Response()

    monkeypatch.setattr(run_ms.requests, "get", get)
    assert run_ms.fetch_pool_names() == {111: "Foundry USA"}


def test_main_parses_run_ms_arguments():
    calls = []
    module = SimpleNamespace(DATA_DIR=Path("default"), POOL_URL="default-url",
                             MONTH_OUTPUT_PATH=Path("months.json"),
                             run=lambda *args: calls.append(args))

    main.run_ms(module, ["--data-dir", "custom", "--pool-url", "new-url",
                         "--month-out", "by-month.json"])

    assert calls == [(Path("custom"), "new-url", Path("by-month.json"))]


def test_run_writes_monthly_json(tmp_path, monkeypatch):
    monkeypatch.setattr(run_ms, "fetch_pool_names",
                        lambda _url: {44: "AntPool", 111: "Foundry USA"})
    monkeypatch.setattr(run_ms, "aggregate_pool_fees",
                        lambda _dir, _names: {
                            "2024-01": {"pools": {"Foundry USA": 20,
                                                   "AntPool": 5},
                                        "sum": 25},
                        })
    month_output_path = tmp_path / "mempool-space-by-month.json"

    run_ms.run(tmp_path, "pool-url", month_output_path)

    assert json.loads(month_output_path.read_text())["2024-01"]["sum"] == 25
    assert month_output_path.read_text().endswith("\n")