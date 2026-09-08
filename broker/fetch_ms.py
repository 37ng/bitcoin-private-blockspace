# fetch data from mempool.space(ms)

import json
from pathlib import Path
from datetime import datetime, timezone
import time

import requests

API = "https://mempool.space/api/v1/services/accelerator/accelerations/history"
CALL_INTERVAL_SEC = 10
MAX_PAGE_LENGTH = 50
TIMEOUT = 30
HEADERS = {"User-Agent": "bitcoin-private-blockspace/1.0 (research)"}
DATA_DIR = Path(__file__).parent / "data"


def latest_data_month() -> str:
	months = []
	for path in DATA_DIR.glob("*.json"):
		try:
			datetime.strptime(path.stem, "%Y-%m")
		except ValueError:
			continue
		months.append(path.stem)
	if not months:
		raise ValueError(f"no monthly JSON files in {DATA_DIR}")
	return max(months)

# complete month is a month that past its last day
def latest_complete_month(now: datetime | None = None) -> str:
	if now is None:
		now = datetime.now(timezone.utc)
	return previous_month(now.strftime("%Y-%m"))


def next_month(month: str) -> str:
	year, month_number = map(int, month.split("-"))
	return f"{year + month_number // 12:04d}-{month_number % 12 + 1:02d}"


def previous_month(month: str) -> str:
	year, month_number = map(int, month.split("-"))
	return f"{year - (month_number == 1):04d}-{(month_number - 2) % 12 + 1:02d}"


def fetch_ms(month: str) -> None:
	year, month_number = map(int, month.split("-"))
	from_timestamp = int(datetime(year, month_number, 1, tzinfo=timezone.utc).timestamp())
	to_year, to_month_number = map(int, next_month(month).split("-"))
	to_timestamp = int(
		datetime(to_year, to_month_number, 1, tzinfo=timezone.utc).timestamp()
	)
	data = []
	page = 1
	while True:
		print(f"\r{month}: fetching page {page}", end="", flush=True)
		response = requests.get(
			API,
			params={
				"from": from_timestamp,
				"to": to_timestamp,
				"page": page,
				"pageLength": MAX_PAGE_LENGTH,
			},
			headers=HEADERS,
			timeout=TIMEOUT,
		)
		response.raise_for_status()
		time.sleep(CALL_INTERVAL_SEC)
		page_data = response.json()
		if not page_data:
			print()
			break
		page += 1
		data.extend(page_data)
	(DATA_DIR / f"{month}.json").write_text(json.dumps(data, indent=2) + "\n")


def fetch_ms_range(from_month: str, to_month: str) -> None:
	month = to_month
	while month >= from_month:
		fetch_ms(month)
		month = previous_month(month)


def fetch_missing_ms() -> None:
	fetch_ms_range(next_month(latest_data_month()), latest_complete_month())

