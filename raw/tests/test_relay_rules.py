import os

import pytest

import bqio
import config

FIXTURES_ENABLED = os.environ.get("BQ_FIXTURES") == "1"

requires_bigquery = pytest.mark.skipif(
    not FIXTURES_ENABLED,
    reason="set BQ_FIXTURES=1 to run the inline fixtures "
           "(free, scans nothing, needs credentials)")

# --- scripts, built by hand ----------------------------------------------

H20 = "11" * 20
H32 = "22" * 32
KEY33 = "02" + "33" * 32       # a 33-byte pubkey
KEY65 = "04" + "44" * 64       # ...and a 65-byte one
PUSH33 = "21" + KEY33          # the same, with the push opcode in front
PUSH65 = "41" + KEY65

P2PKH = "76a914" + H20 + "88ac"
P2SH = "a914" + H20 + "87"
P2PK = "21" + KEY33 + "ac"
P2PK_UNCOMPRESSED = "41" + KEY65 + "ac"
P2WPKH = "0014" + H20
P2WSH = "0020" + H32
P2TR = "5120" + H32
P2A = "51024e73"                       # witness v1, 2-byte program
WITNESS_V16 = "6002abcd"               # unknown version: standard to create
WITNESS_V0_BAD_SIZE = "0015" + "11" * 21
MULTISIG_1_OF_2 = "51" + PUSH33 + PUSH33 + "52ae"
MULTISIG_1_OF_4 = "51" + PUSH33 * 4 + "54ae"
MULTISIG_3_OF_2 = "53" + PUSH33 + PUSH33 + "52ae"   # m > n: non-standard
P2PK_BAD_HEADER = "21" + "00" + "33" * 32 + "ac"  # header disagrees with size
OP_RETURN = "6a0548656c6c6f"
OP_RETURN_84 = "6a4b" + "aa" * 82      # 84 bytes of scriptPubKey
GARBAGE = "deadbeef"

# Dust thresholds, computed by hand at 3000 sat/kvB:
#   3 * (8 + 1 + len(scriptPubKey) + 148 legacy or 67 witness)
DUST = {
    P2PKH: 546,
    P2SH: 540,
    P2PK: 576,
    P2WPKH: 294,
    P2WSH: 330,
    P2TR: 330,
    P2A: 240,
}


# --- lifting the step's own SQL -------------------------------------------

def _fragment(alias):
    sql = bqio.render("01_tx_base.sql")
    end = f"\n    ) AS {alias}"
    assert end in sql, f"01_tx_base.sql no longer builds a `{alias}` struct"
    head = sql.split(end)[0]
    return head[head.rindex("\n    (\n") + 1:] + "\n    )"


def _outputs_sql(outputs):
    rows = ", ".join(f"STRUCT('{script}' AS script_hex, {value} AS value)"
                     for script, value in outputs)
    return f"SELECT [{rows}] AS outputs"


def _one_struct(sql, column):
    job, result = bqio.run(sql, verbose=False)
    assert (job.total_bytes_processed or 0) == 0, "the fixture must scan nothing"
    return dict(list(result)[0][column])


def classify(outputs):
    sql = (f"WITH t AS ({_outputs_sql(outputs)})\n"
           f"SELECT {_fragment('outs')} AS outs FROM t")
    return _one_struct(sql, "outs")


def classify_inputs(script_hexes):
    rows = ", ".join(f"STRUCT('{s}' AS script_hex)" for s in script_hexes)
    sql = (f"WITH t AS (SELECT [{rows}] AS inputs)\n"
           f"SELECT {_fragment('ins')} AS ins FROM t")
    return _one_struct(sql, "ins")


# --- the standard templates ----------------------------------------------

STANDARD = [P2PKH, P2SH, P2PK, P2PK_UNCOMPRESSED, P2WPKH, P2WSH, P2TR, P2A,
            WITNESS_V16, MULTISIG_1_OF_2, MULTISIG_1_OF_4, OP_RETURN]
NONSTANDARD = [WITNESS_V0_BAD_SIZE, MULTISIG_3_OF_2, P2PK_BAD_HEADER, GARBAGE]


@requires_bigquery
@pytest.mark.parametrize("script", STANDARD)
def test_standard_scripts_carry_no_reason(script):
    assert classify([(script, 100_000)])["nonstandard_outputs"] == 0


@requires_bigquery
@pytest.mark.parametrize("script", NONSTANDARD)
def test_nonstandard_scripts_are_counted(script):
    assert classify([(script, 100_000)])["nonstandard_outputs"] == 1


@requires_bigquery
def test_unknown_witness_version_is_standard_to_create():
    assert classify([(WITNESS_V16, 100_000)])["nonstandard_outputs"] == 0


@requires_bigquery
def test_bare_multisig_n_is_read_off_the_script():
    assert classify([(MULTISIG_1_OF_2, 100_000)])["bare_multisig_max_n"] == 2
    assert classify([(MULTISIG_1_OF_4, 100_000)])["bare_multisig_max_n"] == 4
    assert classify([(P2PKH, 100_000)])["bare_multisig_max_n"] == 0


# --- dust ----------------------------------------------------------------

@requires_bigquery
@pytest.mark.parametrize("script,threshold", sorted(DUST.items()))
def test_dust_threshold_is_exact(script, threshold):
    assert classify([(script, threshold)])["dust_outputs"] == 0
    assert classify([(script, threshold - 1)])["dust_outputs"] == 1


@requires_bigquery
def test_op_return_is_never_dust():
    assert classify([(OP_RETURN, 0)])["dust_outputs"] == 0


@requires_bigquery
def test_bare_multisig_dust_is_counted_apart():
    tiny = MULTISIG_1_OF_2
    out = classify([(tiny, 1)])
    assert out["dust_outputs"] == 1
    assert out["dust_outputs_excl_multisig"] == 0


# --- OP_RETURN counting ---------------------------------------------------

@requires_bigquery
def test_op_return_bytes_are_counted_per_output_and_in_total():
    out = classify([(OP_RETURN_84, 0), (OP_RETURN, 0), (P2PKH, 100_000)])
    assert out["op_return_count"] == 2
    assert out["op_return_max_bytes"] == 84
    assert out["op_return_total_bytes"] == 84 + 7


@requires_bigquery
def test_a_transaction_without_op_return_counts_zero():
    out = classify([(P2PKH, 100_000)])
    assert out["op_return_count"] == 0
    assert out["op_return_max_bytes"] == 0
    assert out["op_return_total_bytes"] == 0


# --- scriptSig ------------------------------------------------------------

@requires_bigquery
def test_scriptsig_size_is_measured_in_bytes():
    out = classify_inputs(["47" + "aa" * 71, "00"])
    assert out["max_scriptsig_bytes"] == 72


@requires_bigquery
def test_a_scriptsig_opening_with_a_real_opcode_is_not_push_only():
    assert classify_inputs(["ac"])["opens_with_nonpush_opcode"] is True
    assert classify_inputs(["4730" + "aa" * 70])["opens_with_nonpush_opcode"] is False
    assert classify_inputs([""])["opens_with_nonpush_opcode"] is False


# --- the date gates -------------------------------------------------------
#
# Every rule below is gated on the release date of the Core version that
# changed it, and only the rule in force on the day of the block is applied.
# These run step 03's own SQL over an inline `tx_base`, so what is tested is
# the pipeline's text, not a description of it.

TX_BASE_COLUMNS = [
    ("tx_hash", "STRING", "'tx'"),
    ("block_number", "INT64", "800000"),
    ("block_timestamp", "TIMESTAMP", "TIMESTAMP('2024-01-01')"),
    ("block_month", "DATE", "DATE '2024-01-01'"),
    ("is_coinbase", "BOOL", "FALSE"),
    ("fee", "INT64", "10000"),
    ("virtual_size", "INT64", "1000"),
    ("serialized_size", "INT64", "1000"),
    ("version", "INT64", "2"),
    ("input_hashes", "ARRAY<STRING>", "ARRAY<STRING>[]"),
    ("bare_multisig_max_n", "INT64", "0"),
    ("op_return_count", "INT64", "0"),
    ("op_return_max_bytes", "INT64", "0"),
    ("op_return_total_bytes", "INT64", "0"),
    ("nonstandard_outputs", "INT64", "0"),
    ("dust_outputs", "INT64", "0"),
    ("dust_outputs_excl_multisig", "INT64", "0"),
    ("max_scriptsig_bytes", "INT64", "100"),
    ("opens_with_nonpush_opcode", "BOOL", "FALSE"),
]


def _tx_base_row(overrides):
    unknown = set(overrides) - {c for c, _t, _d in TX_BASE_COLUMNS}
    assert not unknown, f"tx_base has no column {unknown}"
    return "SELECT " + ", ".join(
        f"{overrides.get(name, default)} AS {name}"
        for name, _type, default in TX_BASE_COLUMNS)


def reasons(*rows):
    fixture = "\n  UNION ALL ".join(_tx_base_row(r) for r in rows)
    sql = bqio.render("03_txs.sql")
    body = sql[sql.index("\nAS\n") + 4:]
    body = body.replace(f"`{config.dst()}.tx_base`", f"(\n  {fixture}\n)")
    job, result = bqio.run(f"SELECT * FROM (\n{body}\n)", verbose=False)
    assert (job.total_bytes_processed or 0) == 0, "the fixture must scan nothing"
    return {row["tx_hash"]: row for row in result}


def at(date, **overrides):
    overrides.setdefault("tx_hash", f"'{date}'")
    overrides["block_timestamp"] = f"TIMESTAMP('{date}')"
    overrides["block_month"] = f"DATE '{date[:8]}01'"
    return overrides


@requires_bigquery
def test_the_minimum_relay_fee_gate_moves_on_the_release_date():
    out = reasons(
        at("2025-09-03", tx_hash="'before'", fee="500", virtual_size="1000"),
        at("2025-09-05", tx_hash="'after'", fee="500", virtual_size="1000"),
        at("2025-09-05", tx_hash="'still_under'", fee="50", virtual_size="1000"),
    )
    assert out["before"]["nonrelay_sub_minrelay"] is True
    assert out["after"]["nonrelay_sub_minrelay"] is False
    assert out["still_under"]["nonrelay_sub_minrelay"] is True


@requires_bigquery
def test_the_datacarrier_gate_moves_on_the_release_date():
    out = reasons(
        at("2025-10-07", tx_hash="'before'", op_return_count="1",
           op_return_max_bytes="84", op_return_total_bytes="84"),
        at("2025-10-09", tx_hash="'after'", op_return_count="1",
           op_return_max_bytes="84", op_return_total_bytes="84"),
        at("2025-10-09", tx_hash="'over_the_new_limit'", op_return_count="3",
           op_return_max_bytes="60000", op_return_total_bytes="120000"),
    )
    assert out["before"]["nonrelay_op_return"] is True
    assert out["after"]["nonrelay_op_return"] is False
    assert out["over_the_new_limit"]["nonrelay_op_return"] is True


@requires_bigquery
def test_a_second_op_return_output_is_non_standard_only_before_v30():
    out = reasons(
        at("2025-10-07", tx_hash="'before'", op_return_count="2",
           op_return_max_bytes="20", op_return_total_bytes="40"),
        at("2025-10-09", tx_hash="'after'", op_return_count="2",
           op_return_max_bytes="20", op_return_total_bytes="40"),
    )
    assert out["before"]["nonrelay_multi_op_return"] is True
    assert out["after"]["nonrelay_multi_op_return"] is False


@requires_bigquery
def test_the_standard_version_range_widens_when_truc_ships():
    out = reasons(
        at("2024-10-03", tx_hash="'v3_before'", version="3"),
        at("2024-10-05", tx_hash="'v3_after'", version="3"),
        at("2024-10-05", tx_hash="'v4_after'", version="4"),
        at("2024-10-05", tx_hash="'v0_after'", version="0"),
    )
    assert out["v3_before"]["nonrelay_version"] is True
    assert out["v3_after"]["nonrelay_version"] is False
    assert out["v4_after"]["nonrelay_version"] is True
    assert out["v0_after"]["nonrelay_version"] is True


@requires_bigquery
def test_the_dust_gate_moves_when_ephemeral_dust_ships():
    out = reasons(
        at("2025-04-14", tx_hash="'before'", dust_outputs="1",
           dust_outputs_excl_multisig="1"),
        at("2025-04-16", tx_hash="'paying'", dust_outputs="1",
           dust_outputs_excl_multisig="1"),
        at("2025-04-16", tx_hash="'two'", dust_outputs="2",
           dust_outputs_excl_multisig="2", fee="0"),
    )
    assert out["before"]["nonrelay_dust"] is True
    assert out["paying"]["nonrelay_dust"] is True      # pays a fee, so not ephemeral
    assert out["two"]["nonrelay_dust"] is True


@requires_bigquery
def test_ephemeral_dust_needs_a_zero_fee_parent_and_a_child():
    out = reasons(
        at("2025-06-01", tx_hash="'parent'", fee="0", dust_outputs="1",
           dust_outputs_excl_multisig="1"),
        at("2025-06-01", tx_hash="'child'", fee="5000", virtual_size="1000",
           input_hashes="['parent']"),
        at("2025-06-01", tx_hash="'lonely'", fee="0", dust_outputs="1",
           dust_outputs_excl_multisig="1"),
    )
    assert out["parent"]["nonrelay_dust"] is False
    assert out["lonely"]["nonrelay_dust"] is True


@requires_bigquery
def test_a_transaction_under_65_non_witness_bytes_is_non_standard():
    out = reasons(
        at("2024-01-01", tx_hash="'tiny'", virtual_size="64", serialized_size="64"),
        at("2024-01-01", tx_hash="'small'", virtual_size="65", serialized_size="65"),
        # A witness transaction: base = (4 * 100 - 200) / 3 = 66 bytes.
        at("2024-01-01", tx_hash="'witness'", virtual_size="100",
           serialized_size="200"),
    )
    assert out["tiny"]["nonrelay_undersized"] is True
    assert out["small"]["nonrelay_undersized"] is False
    assert out["witness"]["nonrelay_undersized"] is False


@requires_bigquery
def test_the_scriptsig_rules_have_no_gate():
    out = reasons(
        at("2023-05-01", tx_hash="'big'", max_scriptsig_bytes="1651"),
        at("2023-05-01", tx_hash="'at_the_limit'", max_scriptsig_bytes="1650"),
        at("2026-05-01", tx_hash="'nonpush'", opens_with_nonpush_opcode="TRUE"),
    )
    assert out["big"]["nonrelay_scriptsig_size"] is True
    assert out["at_the_limit"]["nonrelay_scriptsig_size"] is False
    assert out["nonpush"]["nonrelay_scriptsig_nonpush"] is True


@requires_bigquery
def test_the_truc_size_rules_start_with_core_28():
    out = reasons(
        at("2024-10-05", tx_hash="'too_big'", version="3", virtual_size="10001"),
        at("2024-10-05", tx_hash="'ok'", version="3", virtual_size="10000"),
        at("2024-10-05", tx_hash="'v2_big'", version="2", virtual_size="20000"),
    )
    assert out["too_big"]["nonrelay_truc"] is True
    assert out["ok"]["nonrelay_truc"] is False
    assert out["v2_big"]["nonrelay_truc"] is False


@requires_bigquery
def test_a_truc_child_is_capped_smaller_and_may_not_mix_versions():
    out = reasons(
        at("2025-01-01", tx_hash="'parent'", version="3", virtual_size="500"),
        at("2025-01-01", tx_hash="'fat_child'", version="3", virtual_size="1001",
           input_hashes="['parent']"),
        at("2025-01-01", tx_hash="'v2_parent'", version="2", virtual_size="500"),
        at("2025-01-01", tx_hash="'mixed_child'", version="3", virtual_size="500",
           input_hashes="['v2_parent']"),
    )
    assert out["fat_child"]["nonrelay_truc"] is True
    assert out["mixed_child"]["nonrelay_truc"] is True
    assert out["parent"]["nonrelay_truc"] is False


@requires_bigquery
def test_a_coinbase_transaction_is_never_non_relayable():
    out = reasons(at("2024-01-01", tx_hash="'cb'", is_coinbase="TRUE", fee="0",
                   nonstandard_outputs="3", dust_outputs="2", version="9",
                   virtual_size="30", serialized_size="30"))
    assert out["cb"]["is_nonrelayable"] is False


@requires_bigquery
def test_a_sub_minimum_parent_with_a_paying_child_has_no_reason():
    out = reasons(
        at("2024-01-01", tx_hash="'parent'", fee="0", virtual_size="1000"),
        at("2024-01-01", tx_hash="'child'", fee="10000", virtual_size="1000",
           input_hashes="['parent']"),
    )
    assert out["parent"]["nonrelay_sub_minrelay"] is False
    assert out["parent"]["is_nonrelayable"] is False


@requires_bigquery
def test_an_ordinary_transaction_carries_no_reason_at_all():
    out = reasons(at("2024-06-01", tx_hash="'plain'"))
    row = out["plain"]
    assert row["is_nonrelayable"] is False
    assert not [k for k in row.keys()
                if k.startswith("nonrelay_") and row[k]], "a rule fired on a plain tx"
