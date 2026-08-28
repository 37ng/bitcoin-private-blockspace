"""The acceleration SQL steps: do they render, and does BigQuery accept them?

Two layers, because they cost different things to run.

  Rendering tests are pure Python. They need no credentials and no network,
  so they run everywhere. They catch a placeholder that no longer resolves.

  Dry-run tests ask BigQuery to parse and plan each step. A dry run is free --
  it scans nothing and is not billed -- but it needs credentials, and it needs
  the tables an earlier step (including `raw/`'s `blocks` table) builds. They
  are therefore opt-in:

      BQ_DRY_RUN=1 pytest tests/test_sql_steps.py

  A step whose input table is not built yet is skipped, not failed. Only a
  query BigQuery rejects outright is a failure.
"""

import glob
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "utils"))

import bqio
import config

SQL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sql")

SQL_FILES = sorted(os.path.basename(p)
                   for p in glob.glob(os.path.join(SQL_DIR, "*.sql")))

DRY_RUN_ENABLED = os.environ.get("BQ_DRY_RUN") == "1"

requires_bigquery = pytest.mark.skipif(
    not DRY_RUN_ENABLED,
    reason="set BQ_DRY_RUN=1 to dry-run against BigQuery (free, needs credentials)")


def sql_path(name):
    return os.path.join(SQL_DIR, name)


def test_sql_dir_is_not_empty():
    """A glob that silently matches nothing would make every test below pass."""
    assert len(SQL_FILES) >= 2


@pytest.mark.parametrize("name", SQL_FILES)
def test_every_step_renders(name):
    """No step carries a `${placeholder}` that `config` no longer defines."""
    sql = bqio.render(sql_path(name))
    assert "${" not in sql


@pytest.mark.parametrize("name", SQL_FILES)
def test_accel_steps_write_the_accel_dataset(name):
    """Accelerations tables live in `${accel_dst}`, never in the pipeline's `${dst}`.

    They are fetched from mempool.space, not `crypto_bitcoin`, and
    `raw/delete_dataset.py` drops `${dst}` between months -- a table that
    landed there by mistake would vanish with the rest of the disposable
    pipeline tables.
    """
    sql = bqio.render(sql_path(name))
    assert f"CREATE OR REPLACE TABLE `{config.accel_dst()}." in sql
    assert f"{config.dst()}.acceleration" not in sql


@requires_bigquery
@pytest.mark.parametrize("name", SQL_FILES)
def test_step_dry_runs(name):
    """BigQuery parses and plans the step. Free: a dry run scans nothing."""
    from google.api_core.exceptions import NotFound

    try:
        scanned = bqio.dry_run(bqio.render(sql_path(name)))
    except NotFound as exc:
        pytest.skip(f"needs a table an earlier step builds: "
                    f"{str(exc).splitlines()[0][:80]}")
    assert scanned is not None
