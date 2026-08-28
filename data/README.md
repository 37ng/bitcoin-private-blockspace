# data/

Small, checked-in files that the write-up and any chart read directly. Unlike
`bigquery/out/` and `bigquery/.cache/`, this directory is in git: a reader
should be able to reproduce a figure from it without BigQuery credentials.

| file | written by | holds |
|---|---|---|
| `accelerations_monthly.json` | `bigquery/export_accelerations.py` | out-of-band spend per calendar month |

Only complete months appear. A month is published when some run has read the
whole of it, which is not the same as the month having ended — see
`export_accelerations.py` for why the two are tracked apart. Nothing here is
edited by hand; re-run the exporter instead.
