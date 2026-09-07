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
def test_fetch_ms_converts_year_month_arguments(mock_get):
	response = Mock()
	response.json.return_value = []
	mock_get.return_value = response

	assert fetch_ms.fetch_ms("2024-02", "2024-03") == []
	mock_get.assert_called_once_with(
		fetch_ms.API,
		params={
			"from": 1706745600,
			"to": 1709251200,
			"page": 1,
			"pageLength": fetch_ms.MAX_PAGE_LENGTH,
		},
		headers=fetch_ms.HEADERS,
		timeout=fetch_ms.TIMEOUT,
	)
	response.raise_for_status.assert_called_once_with()
