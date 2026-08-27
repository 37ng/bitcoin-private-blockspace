# Private blockspace

How much Bitcoin block space between January 2023 and now was sold outside the
public fee auction?

Block space normally sells in the open: transactions bid a fee rate, miners
take the highest bids, and the cheapest transaction in a block marks the going
price. Space sold outside that auction leaves a specific mark — it sits in a
full block at a price the public auction would never have cleared, and it is
not explained by relay policy. This repository measures how much of it there
is, per month and per pool, and puts two bounds on what it was worth.

The pipeline lives in [`bigquery/`](bigquery/); its README covers the method,
the assumptions, and what the numbers do and do not say.

## Run it

```bash
uv sync
gcloud auth application-default login
cd bigquery
uv run python -m pytest tests/ -q         # offline, no credentials needed
uv run python run_pipeline.py --dry-run   # what each step would scan
uv run python run_pipeline.py --smoke 2023-04   # one month end to end
uv run python run_pipeline.py             # the full window
```

Then validate and write the outputs:

```bash
uv run python sanity_check.py             # pool shares vs public hashrate data
uv run python validate_against_mempool.py # flagged txs vs mempool.space audits
uv run python export_results.py           # CSVs, headline.json, summary.md
```

## Reading the result

Three numbers are reported at every sensitivity (0.3, 0.5, 0.7), not one. The
threshold decides how much of a discount counts as a discount, and a finding
that exists only at 0.7 is a finding about the threshold. `flag_a_sensitivity`
crosses the three sensitivities with three definitions of a full block; read it
before quoting anything.

The two value bands bound a payment that happened off chain and left no record
here. Neither band measures it.
