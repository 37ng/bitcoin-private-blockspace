import json
import os
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fetch_ms


@patch("fetch_ms.requests.get")
def test_fetch_ms_combines_pages_until_empty_response(mock_get, tmp_path):
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
	for response in responses:
		response.raise_for_status.assert_called_once_with()
