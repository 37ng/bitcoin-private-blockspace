# fetch data from mempool.space(ms)

import argparse
import json
from pathlib import Path
from datetime import datetime, timezone

import requests

API = "https://mempool.space/api/v1/services/accelerator/accelerations/history"
MAX_PAGE_LENGTH = 50
SLEEP_INTERVAL_SEC = 5
TIMEOUT = 30
HEADERS = {"User-Agent": "bitcoin-private-blockspace/1.0 (research)"}
DATA_DIR = Path(__file__).parent / "data"


def fetch_ms(month: str) -> list[dict]:
	year, month_number = map(int, month.split("-"))
	from_timestamp = int(datetime(year, month_number, 1, tzinfo=timezone.utc).timestamp())
	to_timestamp = int(
		datetime(
			year + month_number // 12, month_number % 12 + 1, 1, tzinfo=timezone.utc
		).timestamp()
	)
	data = []
	page = 1
	while True:
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
		page_data = response.json()
		if not page_data:
			(DATA_DIR / f"{month}.json").write_text(json.dumps(data, indent=2) + "\n")
			return data
		data.extend(page_data)
		page += 1


def main() -> None:
	parser = argparse.ArgumentParser()
	parser.add_argument("--month", required=True)
	args = parser.parse_args()

	data = fetch_ms(args.month)
	print(json.dumps(data, indent=2))


if __name__ == "__main__":
	main()

