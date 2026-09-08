import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PROG = os.path.basename(__file__)

GROUPS = [
    ("broker", [
        ("fetch-ms", "broker/fetch_ms.py",
         "download acceleration history from mempool.space"),
        ("run-ms", "broker/run_ms.py",
         "build the out-of-band spend tables"),
        ("export-ms", "broker/export_ms.py",
         "publish the monthly out-of-band spend file"),
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


def usage(stream=sys.stdout):
    print(f"usage: {PROG} <command> [args]\n", file=stream)
    for group, items in GROUPS:
        print(group, file=stream)
        for name, _path, what in items:
            print(f"  {name:20s} {what}", file=stream)
        print(file=stream)
    print(f"`{PROG} <command> --help` lists the flags of one command",
          file=stream)


def run(name, argv):
    script = os.path.join(ROOT, COMMANDS[name])
    sys.path.insert(0, os.path.dirname(script))
    sys.argv = [f"{PROG} {name}"] + argv

    module_name = os.path.splitext(os.path.basename(script))[0]
    spec = importlib.util.spec_from_file_location(module_name, script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
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
