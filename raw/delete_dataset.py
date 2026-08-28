"""Delete the BigQuery working dataset.

    python delete_dataset.py          # asks first
    python delete_dataset.py --yes    # no prompt

Run this after `export_results.py` (or `run_pipeline.py --month`) has
written the month's numbers to local files. The working dataset only ever
holds one month of intermediate tables, so deleting it between months keeps
BigQuery storage at zero rather than growing forever; nothing here scans
data, so it costs nothing to run.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "utils"))

import bqio
import config


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true",
                        help="do not ask before deleting")
    args = parser.parse_args()

    from google.cloud.exceptions import NotFound

    ref = f"{config.PROJECT}.{config.DATASET}"
    try:
        bqio.client().get_dataset(ref)
    except NotFound:
        sys.exit(f"dataset {ref} does not exist")

    if not args.yes and not bqio.confirm(f"delete dataset {ref} and all its tables?"):
        sys.exit("stopped; dataset was not deleted")

    bqio.client().delete_dataset(ref, delete_contents=True, not_found_ok=True)
    print(f"deleted {ref}")


if __name__ == "__main__":
    main()
