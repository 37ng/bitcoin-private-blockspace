# Pipeline output

Tracked in git, and built one month at a time. `run_pipeline.py --month
YYYY-MM` runs that month and `export_results.py` merges it into the JSON
files here: the months already present stay, the month just run replaces its
own rows. Commit the result and the history shows the window growing.

| file | one row per | key a re-run replaces |
|---|---|---|
| `monthly_summary.json` | month | `block_month` |
| `pool_summary.json` | month and pool | `block_month` |
| `low_fee_sensitivity.json` | month and grid cell | `block_month` |
| `low_fee_txs_sample.json` | transaction | `block_month`, then the 5,000 largest kept |

`headline.json` and `summary.md` are derived. They are rewritten in full from
the four files above on every export, so they always describe every month on
disk, not just the one that was run.
