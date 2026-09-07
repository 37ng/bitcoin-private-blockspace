# fetch data from mempool.space(ms)

import argparse
import json
import re
from datetime import datetime, timezone

import requests

API = "https://mempool.space/api/v1/services/accelerator/accelerations/history"
MAX_PAGE_LENGTH = 50
SLEEP_INTERVAL_MS = 10.0
TIMEOUT = 30
HEADERS = {"User-Agent": "bitcoin-private-blockspace/1.0 (research)"}


def month_to_timestamp(month: str) -> int:
	match = re.fullmatch(r"(\d{2}|\d{4})[-/](\d{2})", month)
	if not match:
		raise ValueError("month must use YY-MM, YYYY-MM, YY/MM, or YYYY/MM format")
	year, month_number = (int(part) for part in match.groups())
	if year < 100:
		year += 2000
	return int(datetime(year, month_number, 1, tzinfo=timezone.utc).timestamp())

# fetch mempool.space in memory
def fetch_ms(from_year_month: str, to_year_month: str) -> list[dict]:
	data = []
	page = 1
	while True:
		response = requests.get(
			API,
			params={
				"from": month_to_timestamp(from_year_month),
				"to": month_to_timestamp(to_year_month),
				"page": page,
				"pageLength": MAX_PAGE_LENGTH,
			},
			headers=HEADERS,
			timeout=TIMEOUT,
		)
		response.raise_for_status()
		page_data = response.json()
		if not page_data:
			return data
		data.extend(page_data)
		page += 1


def main() -> None:
	parser = argparse.ArgumentParser()
	parser.add_argument("--from", dest="from_year_month", required=True)
	parser.add_argument("--to", dest="to_year_month", required=True)
	args = parser.parse_args()

	data = fetch_ms(args.from_year_month, args.to_year_month)
	print(json.dumps(data, indent=2))


if __name__ == "__main__":
	main()

