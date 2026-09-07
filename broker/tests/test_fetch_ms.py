import os
import sys

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
