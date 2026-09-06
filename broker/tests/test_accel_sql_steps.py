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
    assert len(SQL_FILES) >= 2


@pytest.mark.parametrize("name", SQL_FILES)
def test_every_step_renders(name):
    sql = bqio.render(sql_path(name))
    assert "${" not in sql


@pytest.mark.parametrize("name", SQL_FILES)
def test_accel_steps_write_the_accel_dataset(name):
    sql = bqio.render(sql_path(name))
    assert f"CREATE OR REPLACE TABLE `{config.accel_dst()}." in sql
    assert f"{config.dst()}.acceleration" not in sql


@requires_bigquery
@pytest.mark.parametrize("name", SQL_FILES)
def test_step_dry_runs(name):
    from google.api_core.exceptions import NotFound

    try:
        scanned = bqio.dry_run(bqio.render(sql_path(name)))
    except NotFound as exc:
        pytest.skip(f"needs a table an earlier step builds: "
                    f"{str(exc).splitlines()[0][:80]}")
    assert scanned is not None
