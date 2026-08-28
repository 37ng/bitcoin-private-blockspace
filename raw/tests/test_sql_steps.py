import glob
import os
import re

import pytest

import bqio
import config

SQL_FILES = sorted(os.path.basename(p)
                   for p in glob.glob(os.path.join(bqio.SQL_DIR, "*.sql")))

# The acceleration steps live in `../acceleration/sql/`, in their own
# dataset, outside the 01-08 pipeline chain -- covered by their own tests in
# `acceleration/tests/test_sql_steps.py` rather than folded in here.

# Steps that read or write the summary tables the band formula feeds.
BAND_STEPS = ("07_revenue_bands.sql", "07c_pool_summary.sql")
DENOMINATOR_STEPS = ("07b_monthly_summary.sql", "07c_pool_summary.sql")

DRY_RUN_ENABLED = os.environ.get("BQ_DRY_RUN") == "1"

requires_bigquery = pytest.mark.skipif(
    not DRY_RUN_ENABLED,
    reason="set BQ_DRY_RUN=1 to dry-run against BigQuery (free, needs credentials)")


def test_sql_dir_is_not_empty():
    assert len(SQL_FILES) >= 13


@pytest.mark.parametrize("name", SQL_FILES)
def test_every_step_renders(name):
    sql = bqio.render(name)
    assert "${" not in sql


@pytest.mark.parametrize("name", SQL_FILES)
def test_filename_matches_its_step_label(name):
    first_line = source(name).splitlines()[0]
    match = re.match(r"-- Step ([0-9a-z]+):", first_line)
    assert match is not None, f"{name} has no step label"
    assert name.split("_")[0] == match.group(1)


def source(name):
    with open(os.path.join(bqio.SQL_DIR, name)) as fh:
        return fh.read()


@pytest.mark.parametrize("name", BAND_STEPS)
def test_band_formula_comes_from_config(name):
    text = source(name)
    assert "${lower_band_sats}" in text
    assert "${upper_band_sats}" in text


@pytest.mark.parametrize("name", BAND_STEPS)
@pytest.mark.parametrize("arithmetic", ("floor_fee_rate - t.effective_fee_rate",
                                        "median_fee_rate * t.virtual_size"))
def test_band_formula_is_not_hand_written(name, arithmetic):
    assert arithmetic not in source(name)


@pytest.mark.parametrize("name", DENOMINATOR_STEPS)
def test_full_block_denominator_requires_a_floor(name):
    text = source(name)
    assert "${low_fee_denominator}" in text
    # ...and no bare `b.is_full` slipped back in beside it.
    assert "b.is_full" not in text
    assert config.FULL_AND_PRICED in bqio.render(name)


@pytest.mark.parametrize("name", DENOMINATOR_STEPS)
def test_full_block_denominator_excludes_nonrelayable(name):
    assert "NOT t.is_nonrelayable" in bqio.render(name)


def test_sensitivity_grid_uses_the_same_tests():
    sql = bqio.render("08_sensitivity.sql")
    assert "f.is_full" in sql
    assert "f.floor_fee_rate IS NOT NULL" in sql
    assert "NOT t.is_nonrelayable" in sql


def test_sensitivity_grid_is_keyed_by_month():
    sql = bqio.render("08_sensitivity.sql")
    assert "GROUP BY t.block_month, f.sensitivity, f.full_weight" in sql
    assert "t.block_month,\n  f.sensitivity" in sql


@requires_bigquery
@pytest.mark.parametrize("name", SQL_FILES)
def test_step_dry_runs(name):
    from google.api_core.exceptions import NotFound

    try:
        scanned = bqio.dry_run(bqio.render(name))
    except NotFound as exc:
        pytest.skip(f"needs a table an earlier step builds: "
                    f"{str(exc).splitlines()[0][:80]}")
    assert scanned is not None
