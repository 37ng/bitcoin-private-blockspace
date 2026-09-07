import os
import sys
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fetch_ms


@pytest.mark.parametrize("month", ["2024-02", "24-02", "2024/02", "24/02"])
def test_month_to_timestamp_accepts_supported_formats(month):
	assert fetch_ms.month_to_timestamp(month) == 1706745600


@pytest.mark.parametrize("month", ["2024-2", "2024.02", "2024-13"])
def test_month_to_timestamp_rejects_invalid_month(month):
	with pytest.raises(ValueError):
		fetch_ms.month_to_timestamp(month)


@patch("fetch_ms.requests.get")
def test_fetch_ms_combines_pages_until_empty_response(mock_get):
	responses = [Mock(), Mock(), Mock()]
	responses[0].json.return_value = [{"txid": "first"}]
	responses[1].json.return_value = [{"txid": "second"}]
	responses[2].json.return_value = []
	mock_get.side_effect = responses

	assert fetch_ms.fetch_ms("2024-02", "2024-03") == [
		{"txid": "first"},
		{"txid": "second"},
	]
	assert [call.kwargs["params"]["page"] for call in mock_get.call_args_list] == [1, 2, 3]
	for response in responses:
		response.raise_for_status.assert_called_once_with()
