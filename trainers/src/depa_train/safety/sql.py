"""Static SQL policy for ``sql/duckdb``: a single read-only query over registered input views.

The query is parsed with sqlglot (DuckDB dialect) and rejected unless it is exactly one SELECT /
set operation whose every table reference is a registered view or a CTE, with no table functions,
file paths, URLs, qualified names, DDL/DML, COPY/ATTACH/INSTALL/LOAD/SET/PRAGMA and no function
outside the allowlist. DuckDB itself is additionally locked down at run time (no external access,
no extensions, configuration locked), so a policy gap alone does not open file or network access.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

MAX_QUERY_CHARS = 65_536

_FORBIDDEN_NODE_NAMES = (
    "Command",
    "Copy",
    "Attach",
    "Detach",
    "Install",
    "Set",
    "Pragma",
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Create",
    "Drop",
    "Alter",
    "Into",
    "Use",
    "Describe",
    "Summarize",
    "Transaction",
    "Commit",
    "Rollback",
    "LoadData",
    "Export",
    "Refresh",
    "Cache",
    "Uncache",
    "Analyze",
    "Show",
    "Grant",
    "Revoke",
    "TruncateTable",
    "Kill",
    "Placeholder",
    "Parameter",
)
FORBIDDEN_NODES = tuple(getattr(exp, n) for n in _FORBIDDEN_NODE_NAMES if hasattr(exp, n))

# Functions DuckDB provides that sqlglot does not map to a typed expression. Anything not listed
# here (and not a typed sqlglot function) is rejected, e.g. read_text, glob, query, getenv,
# current_setting, setseed, duckdb_* introspection and every *_scan table function.
SAFE_UNTYPED_FUNCTIONS = frozenset(
    """
    sha256 hash md5_number list_value string_split string_split_regex regexp_extract regexp_matches
    regexp_replace regexp_full_match strptime strftime date_diff datediff date_sub date_add epoch
    epoch_ms greatest least nullif ifnull round floor ceil ceiling abs sign ln log log2 log10 sqrt cbrt
    pow power exp lower upper lcase ucase trim ltrim rtrim length len substr substring concat concat_ws
    left right replace starts_with ends_with contains prefix suffix array_length list_contains
    list_extract list_concat row_number rank dense_rank percent_rank cume_dist ntile lag lead first
    last first_value last_value nth_value arg_max arg_min median quantile_cont quantile_disc mode
    stddev stddev_pop stddev_samp variance var_pop var_samp corr covar_pop covar_samp bool_and bool_or
    string_agg list count_if sum avg min max count try_cast year month day hour minute second
    dayofweek dayofyear week weekofyear quarter date_trunc date_part make_date make_timestamp
    to_timestamp isnan isinf isfinite bit_count xor format printf lpad rpad repeat reverse
    strip_accents ascii chr unicode instr strpos position translate split_part array_to_string
    list_aggregate list_sort list_distinct unnest struct_pack struct_extract map_extract cardinality
    """.split()
)
FORBIDDEN_FUNCTION_PREFIXES = ("read_", "duckdb_", "pragma_", "parquet_", "sqlite_", "postgres_", "mysql_")


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return node.sql_name().lower()


def check_query(query: str, views: set[str]) -> list[str]:
    """Return policy violations for ``query`` given the registered view names (empty = allowed)."""
    if len(query) > MAX_QUERY_CHARS:
        return [f"query exceeds {MAX_QUERY_CHARS} characters"]
    try:
        statements = [s for s in sqlglot.parse(query, read="duckdb") if s is not None]
    except sqlglot.errors.SqlglotError as exc:
        return [f"query does not parse: {str(exc).splitlines()[0]}"]
    if len(statements) != 1:
        return ["exactly one SQL statement is allowed"]
    root = statements[0]
    if not isinstance(root, exp.Query):
        return [f"only SELECT queries are allowed (got {type(root).__name__.upper()})"]

    errors: list[str] = []
    allowed_views = {v.lower() for v in views}
    cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    for node in root.walk():
        if isinstance(node, FORBIDDEN_NODES):
            errors.append(f"{type(node).__name__.upper()} is not allowed")
    for table in root.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            errors.append("table functions are not allowed in FROM (only registered views)")
            continue
        if table.args.get("db") is not None or table.args.get("catalog") is not None:
            errors.append(f"qualified table names are not allowed ('{table.sql(dialect='duckdb')}')")
            continue
        name = table.name.lower()
        if name not in allowed_views and name not in cte_names:
            errors.append(f"unknown table '{table.name}' (allowed: registered views and CTEs)")
    for func in root.find_all(exp.Func):
        name = _function_name(func)
        if type(func).__name__.startswith("Read") or name.startswith(FORBIDDEN_FUNCTION_PREFIXES):
            errors.append(f"function '{name}' is not allowed")
        elif isinstance(func, exp.Anonymous) and name not in SAFE_UNTYPED_FUNCTIONS:
            errors.append(f"function '{name}' is not on the SQL function allowlist")
    return sorted(set(errors))
