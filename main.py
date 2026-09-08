import argparse
import importlib.util
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
PROG = os.path.basename(__file__)

GROUPS = [
    ("broker", [
        ("fetch-ms", "broker/fetch_ms.py",
         "download acceleration history from mempool.space"),
        ("run-ms", "broker/run_ms.py",
         "build the out-of-band spend tables"),
    ]),
    ("chain", [
        ("run-pipeline", "chain/run_pipeline.py",
         "the block space pipeline, end to end"),
        ("effective-fee", "chain/effective_fee.py",
         "the union-find pass on its own"),
        ("export-results", "chain/export_results.py",
         "write the monthly result files"),
        ("export-onchain-fee", "chain/export_onchain_monthly_fee.py",
         "publish the monthly on-chain fee file"),
        ("sanity-check", "chain/sanity_check.py",
         "pool attribution quality and monthly shares"),
        ("validate-mempool", "chain/validate_against_mempool.py",
         "check low-fee blocks against mempool.space"),
    ]),
    ("utils", [
        ("refresh-pools", "utils/refresh_pools.py",
         "update pools.json from upstream"),
        ("delete-dataset", "utils/delete_dataset.py",
         "drop the BigQuery working dataset"),
    ]),
]

COMMANDS = {name: path for _group, items in GROUPS for name, path, _what in items}


def month(value):
    try:
        datetime.strptime(value, "%Y-%m")
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value} is not a YYYY-MM month")
    return value


def fetch_ms(module, argv):
    parser = argparse.ArgumentParser(
        description="with no flag, fetch every month after the newest file in "
                    "broker/data, up to the last complete month")
    parser.add_argument("--month", type=month, metavar="YYYY-MM",
                        help="fetch this month alone")
    parser.add_argument("--from", dest="from_month", type=month,
                        metavar="YYYY-MM", help="fetch this month and later")
    parser.add_argument("--to", dest="to_month", type=month, metavar="YYYY-MM",
                        help="stop after this month")
    args = parser.parse_args(argv)

    if args.month and (args.from_month or args.to_month):
        parser.error("--month does not go with --from or --to")
    if bool(args.from_month) != bool(args.to_month):
        parser.error("--from and --to go together")

    if args.month:
        module.fetch_ms(args.month)
    elif args.from_month:
        module.fetch_ms_range(args.from_month, args.to_month)
    else:
        module.fetch_missing_ms()


HANDLERS = {"fetch-ms": fetch_ms}


def usage(stream=sys.stdout):
    print(f"usage: {PROG} <command> [args]\n", file=stream)
    for group, items in GROUPS:
        print(group, file=stream)
        for name, _path, what in items:
            print(f"  {name:20s} {what}", file=stream)
        print(file=stream)
    print(f"`{PROG} <command> --help` lists the flags of one command",
          file=stream)


def load(script):
    sys.path.insert(0, os.path.dirname(script))
    module_name = os.path.splitext(os.path.basename(script))[0]
    spec = importlib.util.spec_from_file_location(module_name, script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def run(name, argv):
    script = os.path.join(ROOT, COMMANDS[name])
    sys.argv = [f"{PROG} {name}"] + argv
    module = load(script)
    handler = HANDLERS.get(name)
    if handler:
        return handler(module, argv)
    return module.main()


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        usage()
        return 0

    name, rest = argv[0], argv[1:]
    if name not in COMMANDS:
        print(f"unknown command: {name}\n", file=sys.stderr)
        usage(sys.stderr)
        return 2

    return run(name, rest) or 0


if __name__ == "__main__":
    sys.exit(main())
