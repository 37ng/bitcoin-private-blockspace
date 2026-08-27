"""Configuration for the private-blockspace pipeline.

All policy dates and thresholds live here. Every SQL step reads these
values through the templating in `bqio.render`, so a threshold is defined
once and cannot drift between steps.
"""

import os

# --- BigQuery targets -----------------------------------------------------

PROJECT = os.environ.get("BQ_PROJECT", "bitcoin-private-blockspace")
DATASET = os.environ.get("BQ_DATASET", "blockspace")
LOCATION = os.environ.get("BQ_LOCATION", "US")

SOURCE = "bigquery-public-data.crypto_bitcoin"

# --- Study window ---------------------------------------------------------

START_DATE = os.environ.get("START_DATE", "2023-01-01")
# Open-ended by default: "now" is whatever the public dataset holds.
END_DATE = os.environ.get("END_DATE", "2100-01-01")


def month_of(date_str: str) -> str:
    """First day of the month, matching the source partition key."""
    return date_str[:8] + "01"


def set_window(start_date: str, end_date: str = None) -> None:
    """Narrow the study window. Used by `run_pipeline.py --smoke`."""
    global START_DATE, END_DATE
    START_DATE = start_date
    if end_date:
        END_DATE = end_date

# --- Policy dates and standardness limits ---------------------------------
# A transaction is "non-relayable" when a default-configured node of the day
# would refuse to relay it. Such a transaction cannot reach a miner through
# the public mempool, so it did not buy its block space in the public auction.

# Core v30 (2025-10-08) removed the OP_RETURN datacarrier limit.
DATACARRIER_LIFT_DATE = "2025-10-08"
DATACARRIER_LIMIT_BEFORE = 83      # bytes of scriptPubKey (OP_RETURN + 80 data)
DATACARRIER_LIMIT_AFTER = 100_000  # bytes

# Core 29.1 (2025-09-04) lowered the default minimum relay fee.
MIN_RELAY_CHANGE_DATE = "2025-09-04"
MIN_RELAY_BEFORE = 1.0  # sat/vB
MIN_RELAY_AFTER = 0.1   # sat/vB

# Core 28 (2024-10-04) shipped 1p1c package relay. After this date a
# sub-minimum parent with a paying child propagates publicly. Recorded for
# reporting; the "paying child" carve-out is applied over the whole window
# because miner-side CPFP mempool policy predates package relay.
PACKAGE_RELAY_DATE = "2024-10-04"

# Standard transaction size ceiling: 400,000 WU == 100,000 vB.
MAX_STANDARD_VSIZE = 100_000

# Bare multisig is standard up to 3 pubkeys.
BARE_MULTISIG_MAX_N = 3

# --- Block floor and fullness --------------------------------------------

# The floor of a block is derived from its neighbours, never from itself.
NEIGHBOUR_OFFSETS = (-3, -2, -1, 1, 2, 3)
FLOOR_PERCENTILE = 0.05

# A block is weight-full at or above this weight. The maximum is 4,000,000 WU
# but a block cannot always fit one more transaction, so the test has slack.
BLOCK_WEIGHT_FULL = 3_900_000
# ...and it counts as full only when demand was sustained around it.
FULL_NEIGHBOURS_REQUIRED = 4  # of the 6 neighbours

# --- Flag A sensitivity ---------------------------------------------------

SENSITIVITIES = (0.3, 0.5, 0.7)
FULLNESS_GRID = (3_850_000, 3_900_000, 3_950_000)

PRIMARY_SENSITIVITY = 0.5  # headline number; always report the grid with it

# --- Revenue bands --------------------------------------------------------
# The two bounds on what flagged space was worth. See `10_revenue_bands.sql`
# for what each band means. They live here because step 07 and step 07c both
# compute them, and a formula written twice is a formula that drifts.
#
# Both expressions assume the step aliases `txs` as `t` and `blocks` as `b`.
LOWER_BAND_SATS = ("GREATEST(b.floor_fee_rate - t.effective_fee_rate, 0)"
                   " * t.virtual_size")
UPPER_BAND_SATS = "b.median_fee_rate * t.virtual_size"

# A full block with no floor can hold no flagged transaction, so it belongs in
# no denominator. `is_full` is set from weight and neighbour count alone and
# does not imply a floor, so every share must test for both.
FULL_AND_PRICED = "b.is_full AND b.floor_fee_rate IS NOT NULL"

# --- Pipeline mechanics ---------------------------------------------------

# Blocks per chunk when the union-find step streams transactions to Python.
UNIONFIND_CHUNK_BLOCKS = int(os.environ.get("UNIONFIND_CHUNK_BLOCKS", "5000"))
# Package rows held in memory before a load job is sent.
UNIONFIND_FLUSH_ROWS = int(os.environ.get("UNIONFIND_FLUSH_ROWS", "1000000"))

# Price used only to print an estimate before a run. BigQuery on-demand
# pricing, US multi-region, USD per TiB scanned.
USD_PER_TIB = 6.25

OUT_DIR = os.environ.get("OUT_DIR", "out")
CACHE_DIR = os.environ.get("CACHE_DIR", ".cache")


def dst() -> str:
    """Fully qualified destination dataset."""
    return f"{PROJECT}.{DATASET}"


def template_vars() -> dict:
    """Values injected into every SQL step."""
    return {
        "dst": dst(),
        "src": SOURCE,
        "start_date": START_DATE,
        "start_month": month_of(START_DATE),
        "end_date": END_DATE,
        "end_month": month_of(END_DATE),
        "datacarrier_lift_date": DATACARRIER_LIFT_DATE,
        "datacarrier_limit_before": DATACARRIER_LIMIT_BEFORE,
        "datacarrier_limit_after": DATACARRIER_LIMIT_AFTER,
        "min_relay_change_date": MIN_RELAY_CHANGE_DATE,
        "min_relay_before": MIN_RELAY_BEFORE,
        "min_relay_after": MIN_RELAY_AFTER,
        "package_relay_date": PACKAGE_RELAY_DATE,
        "max_standard_vsize": MAX_STANDARD_VSIZE,
        "bare_multisig_max_n": BARE_MULTISIG_MAX_N,
        "floor_percentile": FLOOR_PERCENTILE,
        "block_weight_full": BLOCK_WEIGHT_FULL,
        "full_neighbours_required": FULL_NEIGHBOURS_REQUIRED,
        "neighbour_offsets": ", ".join(str(o) for o in NEIGHBOUR_OFFSETS),
        "sens_low": SENSITIVITIES[0],
        "sens_mid": SENSITIVITIES[1],
        "sens_high": SENSITIVITIES[2],
        "primary_sensitivity": PRIMARY_SENSITIVITY,
        "sensitivity_grid": ", ".join(str(s) for s in SENSITIVITIES),
        "fullness_grid": ", ".join(str(w) for w in FULLNESS_GRID),
        "lower_band_sats": LOWER_BAND_SATS,
        "upper_band_sats": UPPER_BAND_SATS,
        "full_and_priced": FULL_AND_PRICED,
    }
