"""``sql/duckdb@1``: join / select input tables with one read-only SQL query (replaces v1 Spark)."""

from __future__ import annotations

from pydantic import Field

from ...data import FileTable
from ...registry import TABLE, Plugin, ValidationContext
from ...safety.sql import check_query
from ...types import Alias, ColumnName, Strict


class SqlTable(FileTable):
    name: Alias = Field(description="view name used in the query")


class SqlConfig(Strict):
    tables: list[SqlTable] = Field(min_length=1, max_length=32)
    query: str = Field(min_length=1, max_length=65_536)
    privacy_unit: ColumnName | None = Field(
        default=None,
        description="column identifying the individual; the run fails if a value repeats or is null",
    )
    threads: int | None = Field(default=None, ge=1, le=256)
    memory_limit_mb: int = Field(default=8192, ge=64, le=1_048_576)


class SqlDuckDB(Plugin):
    name = "sql/duckdb"
    version = "1.0.0"
    Config = SqlConfig
    produces = TABLE
    summary = "Read-only SQL over registered input views (joins, feature selection)"
    wraps = "DuckDB (locked down) + sqlglot policy"
    impl = "depa_train.plugins.sql_duckdb.impl:run"
    preload_modules = ("pyarrow.csv", "pyarrow.parquet", "pyarrow.json", "pyarrow.feather")

    def check(self, cfg: SqlConfig, v: ValidationContext) -> None:
        names = [t.name for t in cfg.tables]
        for dup in sorted({n for n in names if names.count(n) > 1}):
            v.error("tables", f"duplicate view name '{dup}'")
        for i, table in enumerate(cfg.tables):
            v.require_alias(table.input, f"tables[{i}].input")
        for err in check_query(cfg.query, set(names)):
            v.error("query", err)
        if v.dp and cfg.privacy_unit is None:
            v.error(
                "privacy_unit",
                "required when privacy.mode is 'dp': DP guarantees are per row, so each individual "
                "must contribute at most one row",
            )
