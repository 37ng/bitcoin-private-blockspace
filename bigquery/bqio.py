"""BigQuery helpers: SQL templating, dry-run cost reports, job execution.

Every step is a `.sql` file under `sql/`. Placeholders are `${name}` and are
filled from `config.template_vars()` plus the pool tables built by `pools.py`,
so a threshold written in `config.py` reaches the SQL without being retyped.
"""

import os
import string
import sys
import time

import config
import pools

SQL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sql")

_client = None


def client():
    """Lazy BigQuery client, so the pure-Python tests need no credentials."""
    global _client
    if _client is None:
        from google.cloud import bigquery
        _client = bigquery.Client(project=config.PROJECT,
                                  location=config.LOCATION)
    return _client


def sql_vars(extra=None):
    v = config.template_vars()
    v["pool_tags"] = pools.tag_struct_sql()
    v["pool_addresses"] = pools.address_struct_sql()
    v["pool_ids"] = pools.pool_id_struct_sql()
    if extra:
        v.update(extra)
    return v


def render(name, extra=None):
    """Read `sql/<name>` and substitute `${...}` placeholders."""
    path = name if os.path.isabs(name) else os.path.join(SQL_DIR, name)
    with open(path) as fh:
        text = fh.read()
    try:
        return string.Template(text).substitute(sql_vars(extra))
    except KeyError as exc:
        raise KeyError(f"{os.path.basename(path)}: no value for {exc}") from exc


def render_string(text, extra=None):
    """Substitute `${...}` placeholders in an inline SQL string."""
    return string.Template(text).substitute(sql_vars(extra))


def human_bytes(n):
    if n is None:
        return "unknown"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0


def usd(n_bytes):
    if not n_bytes:
        return 0.0
    return n_bytes / (1024 ** 4) * config.USD_PER_TIB


def dry_run(sql):
    """Bytes this query would scan. Costs nothing."""
    from google.cloud import bigquery
    job = client().query(
        sql,
        job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False),
    )
    return job.total_bytes_processed


def run(sql, label="query", verbose=True):
    """Execute a query and report what it scanned and how long it took."""
    started = time.time()
    job = client().query(sql)
    result = job.result()
    scanned = job.total_bytes_processed or 0
    if verbose:
        print(f"    {label}: scanned {human_bytes(scanned)} "
              f"(${usd(scanned):.2f}) in {time.time() - started:.1f}s")
    return job, result


def run_file(name, label=None, extra=None, verbose=True):
    return run(render(name, extra), label or name, verbose=verbose)


def scalar(sql):
    """First column of the first row."""
    for row in client().query(sql).result():
        return row[0]
    return None


def rows(sql):
    return list(client().query(sql).result())


def stream(sql):
    """Row iterator, for result sets too large to hold in memory at once."""
    return client().query(sql).result()


def table_exists(table):
    from google.cloud.exceptions import NotFound
    try:
        client().get_table(f"{config.dst()}.{table}")
        return True
    except NotFound:
        return False


def ensure_dataset(dataset=None):
    """Create the given dataset (default `config.DATASET`) if it is missing."""
    from google.cloud import bigquery
    from google.cloud.exceptions import NotFound
    ref = f"{config.PROJECT}.{dataset or config.DATASET}"
    try:
        client().get_dataset(ref)
    except NotFound:
        ds = bigquery.Dataset(ref)
        ds.location = config.LOCATION
        client().create_dataset(ds)
        print(f"created dataset {ref} in {config.LOCATION}")


def confirm(prompt):
    """Ask before spending money. Non-interactive runs must pass --yes."""
    if not sys.stdin.isatty():
        return False
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
