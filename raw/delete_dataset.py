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
