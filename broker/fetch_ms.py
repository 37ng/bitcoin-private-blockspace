# fetch data from mempool.space(ms)

import argparse
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
			return
		(DATA_DIR / f"{month}.json").write_text(json.dumps(data, indent=2) + "\n")
		data.extend(page_data)
		page += 1


def main() -> None:
	parser = argparse.ArgumentParser()

	# from/to are inclusive
	parser.add_argument("--from", dest="from_month", required=True)
	parser.add_argument("--to", dest="to_month", required=True)
	args = parser.parse_args()

	month = args.to_month
	while month >= args.from_month:
		fetch_ms(month)
		month = previous_month(month)


if __name__ == "__main__":
	main()

