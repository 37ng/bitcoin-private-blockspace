import json
import os
import sys
from unittest.mock import Mock, call, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fetch_ms


def test_next_month_rolls_over_december():
	assert fetch_ms.next_month("2023-12") == "2024-01"


def test_previous_month_rolls_over_january():
	assert fetch_ms.previous_month("2024-01") == "2023-12"


def test_latest_data_month_uses_latest_monthly_json_file(tmp_path):
	(tmp_path / "2024-01.json").write_text("[]")
	(tmp_path / "2024-10.json").write_text("[]")
	(tmp_path / "notes.json").write_text("[]")

	with patch.object(fetch_ms, "DATA_DIR", tmp_path):
		assert fetch_ms.latest_data_month() == "2024-10"


def test_latest_complete_month_excludes_current_month():
	now = fetch_ms.datetime(2026, 9, 7, tzinfo=fetch_ms.timezone.utc)

	assert fetch_ms.latest_complete_month(now) == "2026-08"


@patch("fetch_ms.requests.get")
@patch("fetch_ms.print")
@patch("fetch_ms.time.sleep")
def test_fetch_ms_combines_pages_until_empty_response(
	mock_sleep, mock_print, mock_get, tmp_path
):
	responses = [Mock(), Mock(), Mock()]
	responses[0].json.return_value = [{"txid": "first"}]
	responses[1].json.return_value = [{"txid": "second"}]
	responses[2].json.return_value = []
	mock_get.side_effect = responses

	with patch.object(fetch_ms, "DATA_DIR", tmp_path):
		fetch_ms.fetch_ms("2024-02")
	assert json.loads((tmp_path / "2024-02.json").read_text()) == [
		{"txid": "first"},
		{"txid": "second"},
	]
	assert [call.kwargs["params"]["page"] for call in mock_get.call_args_list] == [1, 2, 3]
	assert [call.kwargs["params"]["from"] for call in mock_get.call_args_list] == [1706745600] * 3
	assert [call.kwargs["params"]["to"] for call in mock_get.call_args_list] == [1709251200] * 3
	assert mock_sleep.call_args_list == [call(fetch_ms.CALL_INTERVAL_SEC)] * 3
	assert mock_print.call_args_list == [
		call("\r2024-02: fetching page 1", end="", flush=True),
		call("\r2024-02: fetching page 2", end="", flush=True),
		call("\r2024-02: fetching page 3", end="", flush=True),
		call(),
	]
	for response in responses:
		response.raise_for_status.assert_called_once_with()


@patch("fetch_ms.fetch_ms")
def test_fetch_ms_range_fetches_months_from_to(mock_fetch_ms):
	fetch_ms.fetch_ms_range("2023-01", "2023-02")

	assert mock_fetch_ms.call_args_list == [call("2023-02"), call("2023-01")]


@patch("fetch_ms.fetch_ms_range")
@patch("fetch_ms.latest_complete_month", return_value="2024-02")
@patch("fetch_ms.latest_data_month", return_value="2023-12")
def test_fetch_missing_ms_fetches_after_latest_data_through_complete_month(
	mock_latest_data_month, mock_latest_complete_month, mock_fetch_ms_range
):
	fetch_ms.fetch_missing_ms()

	assert mock_fetch_ms_range.call_args_list == [call("2024-01", "2024-02")]
