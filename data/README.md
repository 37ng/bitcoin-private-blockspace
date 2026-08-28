# data/

Small, checked-in files that the write-up and any chart read directly. Unlike
`bigquery/out/` and `bigquery/.cache/`, this directory is in git: a reader
should be able to reproduce a figure from it without BigQuery credentials.

| file | written by | holds |
|---|---|---|
| `accelerations_monthly.json` | `bigquery/export_accelerations.py` | out-of-band spend per calendar month |

Only complete months appear. The accelerations table holds one contiguous run
of the history, so `MIN(added)` and `MAX(added)` bound a stretch with nothing
missing inside it, and a month is published when that run covers all of it.
See `bigquery/export_accelerations.py` for why that is the whole test.

Nothing here is edited by hand; re-run the exporter instead.
