"""Mining pool attribution.

A block carries no pool field. Attribution uses two marks the pool leaves in
its own coinbase transaction:

  1. a tag in the coinbase scriptSig (`blocks.coinbase_param`, hex encoded);
  2. the payout address of a coinbase output.

The tag is checked first and the address second, which matches the order used
by mempool.space. `sanity_check.py` compares the resulting share per pool
against a public hashrate chart; a share off by more than ~2 points means
attribution is broken and every downstream number is suspect.

The built-in table below covers the pools that mined blocks between 2023 and
2026. `refresh_pools.py` overwrites `pools_known.json` from the public
mempool.space pool list, and that file wins when it is present.
"""

import json
import os

# (pool name, coinbase tags, payout addresses)
# Tags are matched as substrings of the ASCII-decoded coinbase scriptSig.
BUILTIN_POOLS = [
    ("Foundry USA", ["/Foundry USA Pool", "Foundry USA Pool"],
     ["bc1qxhmdufsvnuaaaer4ynz88fspdsxq2h9e9cetdj"]),
    ("AntPool", ["/AntPool/", "Mined by AntPool", "AntPool"],
     ["bc1qvy4074rggkdr2pzn5vpcmsxv0nx5rlm5v3wht0"]),
    ("ViaBTC", ["/ViaBTC/", "viabtc.com"],
     ["1Hz96kJKF2HLPGY15JWLB5m9qGNxvt8tHJ",
      "bc1qc7ec6dgtu9v7dc4vwfsqmpzns3v4tkx5cs29qc"]),
    ("F2Pool", ["/F2Pool/", "七彩神仙鱼", "/fish/"],
     ["1KFHE7w8BhaENAswwryaoccDb6qcT6DbYY",
      "bc1qxpzenn7yaflv8u0v9vfhstt62vvyj0dw0hxpxc"]),
    ("Binance Pool", ["/Binance/", "binance"],
     ["1FZoQTVXVQjTaXAoDNWLZfWiwzhX5cmyMQ",
      "bc1q4vxn43l44h30nkluqfxd9eckf45vr2awz38lwa"]),
    ("Braiins Pool", ["/slush/", "/Braiins Pool/", "braiins.com"],
     ["1CK6KHY6MHgYvmRQ4PAafKYDrg1ejbH1cE"]),
    ("Luxor", ["/Luxor/", "/LuxorTech/", "luxor.tech",
               "Powered by Luxor Tech"], []),
    ("SBI Crypto", ["/SBICrypto.com Pool/", "SBICrypto"], []),
    ("SecPool", ["/SecPool/"], []),
    ("MARA Pool", ["MARA Pool", "/Mara Pool/", "MARA Made in USA",
                   "MARA/", "/MARA"], []),
    ("SpiderPool", ["/SPIDERPOOL/", "spiderpool"], []),
    ("Poolin", ["/poolin.com", "/Poolin/"], []),
    ("BTC.com", ["/BTC.COM/", "btccom"], []),
    ("SECPOOL", ["/SECPOOL/"], []),
    ("ULTIMUSPOOL", ["/ULTIMUSPOOL/", "ultimuspool", "/ultimus/"], []),
    ("Carbon Negative", ["Carbon Negative", "/carbonnegative/"], []),
    ("WhitePool", ["/WhitePool/", "whitepool"], []),
    ("OCEAN", ["/OCEAN.XYZ/", "OCEAN.XYZ", "/ocean.xyz/"], []),
    ("BitFuFu", ["/BitFuFuPool/", "bitfufu"], []),
    ("Terra Pool", ["/TERRAPOOL/", "terrapool"], []),
    ("Titan", ["/Titan.io/", "titan.io"], []),
    ("Mining Squared", ["/Mining Squared/", "MiningSquared", "/bsquared/"], []),
    ("Bitdeer", ["/BitdeerPool/", "bitdeer"], []),
    ("EMCD", ["/EMCDPool/", "/emcd/", "emcd.io"], []),
    ("Pega Pool", ["/pegapool/", "PEGA Pool"], []),
    ("Neopool", ["/Neopool/", "neopool"], []),
    ("Rawpool", ["/Rawpool.com/"], []),
    ("KuCoinPool", ["/KuCoinPool/", "kucoin"], []),
    ("BTC.TOP", ["/BTC.TOP/"], []),
    ("Sigmapool", ["/sigmapool.com/"], []),
    ("1THash", ["/1THash/", "1thash"], []),
    ("NiceHash", ["/NiceHash/", "nicehash"], []),
    ("SoloCK", ["/solo.ckpool.org/", "solo.ckpool"], []),
    ("CKPool", ["/ckpool.org/", "/ckpool/"], []),
    ("Public Pool", ["/public-pool/", "public-pool.io"], []),
    ("Parasite", ["parasite.wtf", "/parasite/"], []),
    ("Bitaxe", ["/bitaxe/"], []),
    ("OKKONG", ["/OKKONG/", "okkong"], []),
    ("Genesis Mining", ["/Genesis/"], []),
    ("Sato Pool", ["/SatoPool/", "/sato/"], []),
    ("Bitcoin Mining Council", ["/BMC/"], []),
]

_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "pools_known.json")


def load_pools():
    """Return [(name, [tags], [addresses])], preferring the refreshed file."""
    if os.path.exists(_JSON_PATH):
        with open(_JSON_PATH) as fh:
            data = json.load(fh)
        pools = []
        for name, entry in sorted(data.items()):
            pools.append((name, entry.get("tags", []), entry.get("addresses", [])))
        if pools:
            return pools
    return BUILTIN_POOLS


def _sql_string(value: str) -> str:
    """Quote a Python string as a BigQuery string literal."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def tag_struct_sql() -> str:
    """`UNNEST([...])` body mapping a coinbase tag to a pool name.

    Tags are emitted lowercased and matched against a lowercased coinbase
    text, because a hand-kept table gets the case wrong more often than two
    pools collide on a lowercased tag. Longer tags win, so `/Braiins Pool/`
    beats a bare `/slush/` substring.
    """
    rows = []
    seen = set()
    for name, tags, _addr in load_pools():
        for tag in tags:
            lowered = tag.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            rows.append(
                f"STRUCT({_sql_string(name)} AS pool_name, "
                f"{_sql_string(lowered)} AS tag)")
    if not rows:
        raise RuntimeError("no pool tags configured")
    return "[\n    " + ",\n    ".join(rows) + "\n  ]"


def address_struct_sql() -> str:
    """`UNNEST([...])` body mapping a coinbase payout address to a pool name."""
    owners = {}
    for name, _tags, addrs in load_pools():
        for addr in addrs:
            if not addr or " " in addr:  # skip placeholders
                continue
            owners.setdefault(addr, set()).add(name)
    rows = []
    for name, _tags, addrs in load_pools():
        for addr in addrs:
            if addr not in owners or len(owners[addr]) > 1:
                continue  # ambiguous address attributes nothing
            rows.append(
                f"STRUCT({_sql_string(name)} AS pool_name, "
                f"{_sql_string(addr)} AS address)")
    if not rows:
        # An empty array literal needs an explicit type in BigQuery.
        return ("ARRAY<STRUCT<pool_name STRING, address STRING>>[]")
    return "[\n    " + ",\n    ".join(rows) + "\n  ]"
