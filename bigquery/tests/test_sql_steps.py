"""The SQL steps: do they render, and does BigQuery accept them?

Two layers, because they cost different things to run.

  Rendering tests are pure Python. They need no credentials and no network,
  so they run everywhere. They catch a placeholder that no longer resolves
  and a formula that someone retyped instead of sharing.

  Dry-run tests ask BigQuery to parse and plan each step. A dry run is free —
  it scans nothing and is not billed — but it needs credentials, and it needs
  the tables an earlier step builds. They are therefore opt-in:

      BQ_DRY_RUN=1 pytest tests/test_sql_steps.py

  A step whose input table is not built yet is skipped, not failed. Only a
  query BigQuery rejects outright is a failure.
"""

import glob
import os
import re

import pytest

import bqio
import config

SQL_FILES = sorted(os.path.basename(p)
                   for p in glob.glob(os.path.join(bqio.SQL_DIR, "*.sql")))

# The acceleration steps live in `sql/accelerations/`, in their own dataset,
# outside the 01-08 pipeline chain -- covered by their own tests below rather
# than folded into SQL_FILES's numbered-step checks.
ACCEL_SQL_FILES = sorted(
    "accelerations/" + os.path.basename(p)
    for p in glob.glob(os.path.join(bqio.SQL_DIR, "accelerations", "*.sql")))

# Steps that read or write the summary tables the band formula feeds.
BAND_STEPS = ("07_revenue_bands.sql", "07c_pool_summary.sql")
DENOMINATOR_STEPS = ("07b_monthly_summary.sql", "07c_pool_summary.sql")

DRY_RUN_ENABLED = os.environ.get("BQ_DRY_RUN") == "1"

requires_bigquery = pytest.mark.skipif(
    not DRY_RUN_ENABLED,
    reason="set BQ_DRY_RUN=1 to dry-run against BigQuery (free, needs credentials)")


def test_sql_dir_is_not_empty():
    """A glob that silently matches nothing would make every test below pass."""
    assert len(SQL_FILES) >= 13
    assert len(ACCEL_SQL_FILES) >= 2


@pytest.mark.parametrize("name", SQL_FILES + ACCEL_SQL_FILES)
def test_every_step_renders(name):
    """No step carries a `${placeholder}` that `config` no longer defines."""
    sql = bqio.render(name)
    assert "${" not in sql


@pytest.mark.parametrize("name", ACCEL_SQL_FILES)
def test_accel_steps_write_the_accel_dataset(name):
    """Accelerations tables live in `${accel_dst}`, never in the pipeline's `${dst}`.

    They are fetched from mempool.space, not `crypto_bitcoin`, and
    `delete_dataset.py` drops `${dst}` between months -- a table that landed
    there by mistake would vanish with the rest of the disposable pipeline
    tables.
    """
    sql = bqio.render(name)
    assert f"CREATE OR REPLACE TABLE `{config.accel_dst()}." in sql
    assert f"{config.dst()}.acceleration" not in sql


@pytest.mark.parametrize("name", SQL_FILES)
def test_filename_matches_its_step_label(name):
    """A step's number in its filename is the number in its first line.

    The two drifted apart once: the files were numbered 01-13 in run order
    while the comments numbered them by stage, so `10_revenue_bands.sql`
    opened with "Step 07".
    """
    first_line = source(name).splitlines()[0]
    match = re.match(r"-- Step ([0-9a-z]+):", first_line)
    assert match is not None, f"{name} has no step label"
    assert name.split("_")[0] == match.group(1)


def source(name):
    """The step as written, before templating.

    The rendered SQL cannot answer these questions: a hand-typed copy of a
    formula renders byte-identical to the shared one. Only the source shows
    whether the step referred to the constant or retyped it.
    """
    with open(os.path.join(bqio.SQL_DIR, name)) as fh:
        return fh.read()


@pytest.mark.parametrize("name", BAND_STEPS)
def test_band_formula_comes_from_config(name):
    """Both band steps must share one formula, not retype it.

    Guards the drift between step 07 and step 07c: they computed the same two
    bands from two copies of the same arithmetic.
    """
    text = source(name)
    assert "${lower_band_sats}" in text
    assert "${upper_band_sats}" in text


@pytest.mark.parametrize("name", BAND_STEPS)
@pytest.mark.parametrize("arithmetic", ("floor_fee_rate - t.effective_fee_rate",
                                        "median_fee_rate * t.virtual_size"))
def test_band_formula_is_not_hand_written(name, arithmetic):
    """The arithmetic itself must appear in `config.py` and nowhere else."""
    assert arithmetic not in source(name)


@pytest.mark.parametrize("name", DENOMINATOR_STEPS)
def test_full_block_denominator_requires_a_floor(name):
    """A full block with no floor must stay out of every denominator.

    `is_full` is set from weight and neighbour count alone, so it does not
    imply a floor. A block without one can never reach the numerator, and
    counting it below the line only deflates the share.
    """
    text = source(name)
    assert "${low_fee_denominator}" in text
    # ...and no bare `b.is_full` slipped back in beside it.
    assert "b.is_full" not in text
    assert config.FULL_AND_PRICED in bqio.render(name)


@pytest.mark.parametrize("name", DENOMINATOR_STEPS)
def test_full_block_denominator_excludes_nonrelayable(name):
    """Non-relayable space must stay out of every denominator too.

    Step 06b never marks a non-relayable transaction low-fee, so those vbytes
    can only ever sit below the line. Leaving them there measures low-fee relayable
    space against space that was never in the auction, and quietly deflates
    the share by however much non-relayable traffic the month happened to
    carry.
    """
    assert "NOT t.is_nonrelayable" in bqio.render(name)


def test_sensitivity_grid_uses_the_same_tests():
    """Step 08 spells the tests out under its own aliases; it must still be all three."""
    sql = bqio.render("08_sensitivity.sql")
    assert "f.is_full" in sql
    assert "f.floor_fee_rate IS NOT NULL" in sql
    assert "NOT t.is_nonrelayable" in sql


@requires_bigquery
@pytest.mark.parametrize("name", SQL_FILES + ACCEL_SQL_FILES)
def test_step_dry_runs(name):
    """BigQuery parses and plans the step. Free: a dry run scans nothing."""
    from google.api_core.exceptions import NotFound

    try:
        scanned = bqio.dry_run(bqio.render(name))
    except NotFound as exc:
        pytest.skip(f"needs a table an earlier step builds: "
                    f"{str(exc).splitlines()[0][:80]}")
    assert scanned is not None
