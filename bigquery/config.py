"""Configuration for the private-blockspace pipeline.

All policy dates and thresholds live here. Every SQL step reads these
values through the templating in `bqio.render`, so a threshold is defined
once and cannot drift between steps.
"""

import os

# --- BigQuery targets ----------------------------------------------------

PROJECT = os.environ.get("BQ_PROJECT", "bitcoin-private-blockspace")
DATASET = os.environ.get("BQ_DATASET", "blockspace")
LOCATION = os.environ.get("BQ_LOCATION", "US")

# The accelerations history comes from the mempool.space API, not the
# `crypto_bitcoin` public dataset, and it is slow to (re)crawl. It lives in
# its own dataset so `delete_dataset.py` -- which drops `DATASET` between
# months -- can never take it out with the disposable pipeline tables.
ACCEL_DATASET = os.environ.get("BQ_ACCEL_DATASET", "accelerations")

SOURCE = "bigquery-public-data.crypto_bitcoin"

# --- Study window --------------------------------------------------------

START_DATE = os.environ.get("START_DATE", "2023-01-01")
# Open-ended by default: "now" is whatever the public dataset holds.
END_DATE = os.environ.get("END_DATE", "2100-01-01")

# The source dataset is partitioned by month, and the pipeline aggregates by
# month. So a run should cover exactly one month: set MONTH (env var) or pass
# `--month YYYY-MM` to `run_pipeline.py`.
MONTH = os.environ.get("MONTH")


def month_of(date_str: str) -> str:
    """First day of the month, matching the source partition key."""
    return date_str[:8] + "01"


def set_window(start_date: str, end_date: str = None) -> None:
    """Narrow the study window to an explicit date range."""
    global START_DATE, END_DATE
    START_DATE = start_date
    if end_date:
        END_DATE = end_date


def set_month(month_str: str) -> None:
    """Narrow the study window to one calendar month, e.g. "2023-04"."""
    year, month = int(month_str[:4]), int(month_str[5:7])
    start = f"{year}-{month:02d}-01"
    end = f"{year + (month // 12)}-{month % 12 + 1:02d}-01"
    set_window(start, end)


if MONTH:
    set_month(MONTH)

# --- Policy dates and standardness limits --------------------------------
# A transaction is "non-relayable" when a default-configured node of the day
# would refuse to relay it. Such a transaction cannot reach a miner through
# the public mempool, so it did not buy its block space in the public auction.
#
# Every rule below is gated on the release date of the Core version that
# changed it, and the rule in force on the day of the block is the only one
# applied. When a release loosens a rule, transactions the old rule would have
# caught are treated as relayable from the release date on, even though most
# of the network had not upgraded yet. When a release tightens one, the new
# rule starts on the release date. Anything the rule in force does not
# unambiguously reject stays out of the reasons.

# Core v30 (2025-10-08) raised the OP_RETURN datacarrier limit to the standard
# transaction size, made the limit apply to the sum of the OP_RETURN
# scriptPubKeys in a transaction rather than to each one, and dropped the
# one-OP_RETURN-per-transaction rule.
DATACARRIER_LIFT_DATE = "2025-10-08"
DATACARRIER_LIMIT_BEFORE = 83      # bytes of scriptPubKey (OP_RETURN + 80 data)
DATACARRIER_LIMIT_AFTER = 100_000  # bytes, summed over the OP_RETURN outputs

# Core 29.1 (2025-09-04) lowered the default minimum relay fee.
MIN_RELAY_CHANGE_DATE = "2025-09-04"
MIN_RELAY_BEFORE = 1.0  # sat/vB
MIN_RELAY_AFTER = 0.1   # sat/vB

# Core 28 (2024-10-04) shipped 1p1c package relay. After this date a
# sub-minimum parent with a paying child propagates publicly. Recorded for
# reporting; the "paying child" carve-out is applied over the whole window
# because miner-side CPFP mempool policy predates package relay.
PACKAGE_RELAY_DATE = "2024-10-04"

# Core 28 also made version 3 (TRUC, BIP 431) transactions standard, on the
# same day. Before it, only versions 1 and 2 were standard.
TRUC_STANDARD_DATE = PACKAGE_RELAY_DATE
TX_VERSION_MIN = 1
TX_VERSION_MAX_BEFORE = 2
TX_VERSION_MAX_AFTER = 3

# TRUC's own limits, in force from the same date, for version 3 transactions.
TRUC_VERSION = 3
TRUC_MAX_VSIZE = 10_000        # a version 3 transaction
TRUC_CHILD_MAX_VSIZE = 1_000   # ...one that spends an unconfirmed output
TRUC_ANCESTOR_LIMIT = 2        # itself plus at most one unconfirmed ancestor

# Core 29.0 (2025-04-15) allowed one "ephemeral" dust output: a transaction
# may carry a single dust output if it pays no fee and a child spends the dust
# in the same package. Before it, any dust output was non-standard.
EPHEMERAL_DUST_DATE = "2025-04-15"
MAX_DUST_OUTPUTS_BEFORE = 0
MAX_DUST_OUTPUTS_AFTER = 1

# Dust is an output worth less than it costs to spend, priced at the dust
# relay fee. The spend cost is the size of the input that would spend it:
# 148 bytes for a legacy output, 67 for a witness one (the 75% discount).
DUST_RELAY_FEE_SAT_PER_KVB = 3_000
DUST_SPEND_COST_LEGACY = 148
DUST_SPEND_COST_WITNESS = 67
# A script this long can never be spent, so its outputs are never dust.
MAX_SCRIPT_SIZE = 10_000

# Core 31.0 (2026-04-20) replaced the ancestor and descendant limits with
# cluster limits: a connected component of the mempool may hold 64
# transactions and 101 kvB. A cluster over that limit says that some member
# was refused, but not which one, so nothing is counted from that date on.
CLUSTER_MEMPOOL_DATE = "2026-04-20"
ANCESTOR_LIMIT = 25            # the transaction plus its unconfirmed ancestors
ANCESTOR_SIZE_LIMIT_VB = 101_000

# Standard transaction size ceiling: 400,000 WU == 100,000 vB.
MAX_STANDARD_VSIZE = 100_000
# ...and its floor: a transaction under 65 non-witness bytes is non-standard.
MIN_STANDARD_NONWITNESS_SIZE = 65

# Bare multisig is standard up to 3 pubkeys.
BARE_MULTISIG_MAX_N = 3

# The scriptSig of every input must fit in this, and must be push-only.
MAX_STANDARD_SCRIPTSIG_SIZE = 1_650

# --- Block floor and fullness --------------------------------------------

# The floor of a block is derived from its neighbours, never from itself.
NEIGHBOUR_OFFSETS = (-3, -2, -1, 1, 2, 3)
FLOOR_PERCENTILE = 0.05

# A block is weight-full at or above this weight. The maximum is 4,000,000 WU
# but a block cannot always fit one more transaction, so the test has slack.
BLOCK_WEIGHT_FULL = 3_900_000
# ...and it counts as full only when demand was sustained around it.
FULL_NEIGHBOURS_REQUIRED = 4  # of the 6 neighbours

# --- Low-fee sensitivity --------------------------------------------------

SENSITIVITIES = (0.3, 0.5, 0.7)
FULLNESS_GRID = (3_850_000, 3_900_000, 3_950_000)

PRIMARY_SENSITIVITY = 0.5  # headline number; always report the grid with it

# --- Revenue bands -------------------------------------------------------
# The two bounds on what low-fee space was worth. See `07_revenue_bands.sql`
# for what each band means. They live here because step 07 and step 07c both
# compute them, and a formula written twice is a formula that drifts.
#
# Both expressions assume the step aliases `txs` as `t` and `blocks` as `b`.
LOWER_BAND_SATS = ("GREATEST(b.floor_fee_rate - t.effective_fee_rate, 0)"
                   " * t.virtual_size")
UPPER_BAND_SATS = "b.median_fee_rate * t.virtual_size"

# A full block with no floor can hold no low-fee transaction, so it belongs in
# no denominator. `is_full` is set from weight and neighbour count alone and
# does not imply a floor, so every share must test for both.
FULL_AND_PRICED = "b.is_full AND b.floor_fee_rate IS NOT NULL"

# The space every low-fee share is measured against. The low-fee test never
# fires on non-relayable traffic (step 06b): it never entered the public
# auction, so its price says nothing about a discount. Space that can never
# reach the numerator would only deflate the share, so it stays out of the
# denominator too. Step 08 applies the same three tests under its own aliases.
LOW_FEE_DENOMINATOR = FULL_AND_PRICED + " AND NOT t.is_nonrelayable"

# --- Pipeline mechanics --------------------------------------------------

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


def accel_dst() -> str:
    """Fully qualified accelerations dataset."""
    return f"{PROJECT}.{ACCEL_DATASET}"


def template_vars() -> dict:
    """Values injected into every SQL step."""
    return {
        "dst": dst(),
        "accel_dst": accel_dst(),
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
        "truc_standard_date": TRUC_STANDARD_DATE,
        "tx_version_min": TX_VERSION_MIN,
        "tx_version_max_before": TX_VERSION_MAX_BEFORE,
        "tx_version_max_after": TX_VERSION_MAX_AFTER,
        "truc_version": TRUC_VERSION,
        "truc_max_vsize": TRUC_MAX_VSIZE,
        "truc_child_max_vsize": TRUC_CHILD_MAX_VSIZE,
        "truc_ancestor_limit": TRUC_ANCESTOR_LIMIT,
        "ephemeral_dust_date": EPHEMERAL_DUST_DATE,
        "max_dust_outputs_before": MAX_DUST_OUTPUTS_BEFORE,
        "max_dust_outputs_after": MAX_DUST_OUTPUTS_AFTER,
        "dust_relay_fee": DUST_RELAY_FEE_SAT_PER_KVB,
        "dust_spend_cost_legacy": DUST_SPEND_COST_LEGACY,
        "dust_spend_cost_witness": DUST_SPEND_COST_WITNESS,
        "max_script_size": MAX_SCRIPT_SIZE,
        "cluster_mempool_date": CLUSTER_MEMPOOL_DATE,
        "ancestor_limit": ANCESTOR_LIMIT,
        "ancestor_size_limit_vb": ANCESTOR_SIZE_LIMIT_VB,
        "max_standard_vsize": MAX_STANDARD_VSIZE,
        "min_standard_nonwitness_size": MIN_STANDARD_NONWITNESS_SIZE,
        "bare_multisig_max_n": BARE_MULTISIG_MAX_N,
        "max_standard_scriptsig_size": MAX_STANDARD_SCRIPTSIG_SIZE,
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
        "low_fee_denominator": LOW_FEE_DENOMINATOR,
    }
