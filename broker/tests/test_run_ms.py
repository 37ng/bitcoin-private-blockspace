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
        {"status": "completed", "minedByPoolUniqueId": 999,
         "effectiveFee": 300, "feeDelta": 75},
    ]))

    assert run_ms.aggregate_pool_fees(tmp_path, {44: "AntPool"}) == {
        "AntPool": {
            "effectiveFee": 300,
            "feeDelta": 75,
            "totalTxFee": 375,
        }
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
                             run=lambda data_dir, pool_url:
                             calls.append((data_dir, pool_url)))

    main.run_ms(module, ["--data-dir", "custom", "--pool-url", "new-url"])

    assert calls == [(Path("custom"), "new-url")]