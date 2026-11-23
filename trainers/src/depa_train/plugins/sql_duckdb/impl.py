from __future__ import annotations

import os

import duckdb
import pyarrow as pa
import pyarrow.compute as pc

from ...context import StepContext
from ...data.readers import read_file_table
from ...errors import SafetyError, StepError
from ...safety.sql import check_query
from . import SqlConfig


def _connect(cfg: SqlConfig) -> duckdb.DuckDBPyConnection:
    # Inputs are read by pyarrow and registered as in-memory Arrow views, so DuckDB never needs
    # file or network access: it is disabled before any view exists and the configuration locked.
    return duckdb.connect(
        ":memory:",
        config={
            "enable_external_access": False,
            "autoload_known_extensions": False,
            "autoinstall_known_extensions": False,
            "allow_community_extensions": False,
            "allow_unsigned_extensions": False,
            "python_enable_replacements": False,
            "threads": cfg.threads or min(os.cpu_count() or 1, 32),
            "memory_limit": f"{cfg.memory_limit_mb}MB",
            "lock_configuration": True,
        },
    )


def run(cfg: SqlConfig, ctx: StepContext) -> pa.Table:
    errors = check_query(cfg.query, {t.name for t in cfg.tables})
    if errors:  # validated already; re-checked because this is the security boundary
        raise SafetyError(f"SQL policy violation: {errors[0]}")
    con = _connect(cfg)
    try:
        for table in cfg.tables:
            data = read_file_table(ctx, table)
            con.register(table.name, data)
            ctx.log("view_registered", view=table.name, rows=data.num_rows, columns=data.num_columns)
        try:
            result = con.sql(cfg.query).to_arrow_table()
        except duckdb.Error as exc:
            raise StepError(f"query failed: {type(exc).__name__}") from None
    finally:
        con.close()
    names = result.column_names
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise StepError(f"query result has duplicate column names {duplicates}; alias them explicitly")
    if cfg.privacy_unit is not None:
        if cfg.privacy_unit not in names:
            raise StepError(f"privacy_unit column '{cfg.privacy_unit}' is not in the query result")
        column = result.column(cfg.privacy_unit)
        if column.null_count or pc.count_distinct(column).as_py() != result.num_rows:
            raise StepError(f"privacy_unit '{cfg.privacy_unit}' is not unique per row (or has nulls)")
    ctx.log("query_finished", rows=result.num_rows, columns=result.num_columns)
    return result
