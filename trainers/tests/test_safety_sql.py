"""SQL policy (static) and DuckDB lockdown (runtime, independent of the static policy)."""

from __future__ import annotations

import pytest

from depa_train.safety.sql import check_query

VIEWS = {"t1", "t2"}


@pytest.mark.parametrize(
    ("query", "needle"),
    [
        ("SELECT * FROM read_csv('/etc/passwd')", "table functions are not allowed"),
        ("SELECT * FROM read_csv_auto('/etc/passwd')", "table functions are not allowed"),
        ("SELECT * FROM '/etc/passwd'", "unknown table '/etc/passwd'"),
        ('SELECT * FROM "data.csv"', "unknown table 'data.csv'"),
        ("SELECT * FROM 'https://example.com/x.parquet'", "unknown table"),
        ("SELECT * FROM read_parquet('s3://bucket/x.parquet')", "table functions are not allowed"),
        ("SELECT * FROM glob('/etc/*')", "table functions are not allowed"),
        ("SELECT * FROM query('SELECT 1')", "table functions are not allowed"),
        ("COPY t1 TO '/tmp/exfil.csv'", "only SELECT queries are allowed (got COPY)"),
        ("ATTACH '/tmp/x.db' AS x", "only SELECT queries are allowed (got ATTACH)"),
        ("INSTALL httpfs", "only SELECT queries are allowed (got INSTALL)"),
        ("LOAD httpfs", "only SELECT queries are allowed (got COMMAND)"),
        ("SET enable_external_access = true", "only SELECT queries are allowed (got SET)"),
        ("PRAGMA version", "only SELECT queries are allowed (got PRAGMA)"),
        ("CREATE TABLE z AS SELECT 1", "only SELECT queries are allowed (got CREATE)"),
        ("SELECT 1; DROP TABLE t1", "exactly one SQL statement is allowed"),
        ("SELECT * FROM main.t1", "qualified table names are not allowed"),
        ("SELECT * FROM other_db.main.t1", "qualified table names are not allowed"),
        ("SELECT * FROM secrets", "unknown table 'secrets'"),
        ("SELECT read_text('/etc/passwd')", "function 'read_text' is not allowed"),
        ("SELECT getenv('HOME')", "function 'getenv' is not on the SQL function allowlist"),
        ("SELECT current_setting('home_directory')", "function 'current_setting' is not on the SQL function"),
        ("SELECT * FROM t1 WHERE a IN (SELECT a FROM read_csv('/x'))", "table functions are not allowed"),
        ("SELECT duckdb_secrets()", "function 'duckdb_secrets' is not allowed"),
        ("SELECT * FROM t1 WHERE a = $1", "PLACEHOLDER is not allowed"),
        ("SELEC * FROM t1", "query does not parse"),
    ],
)
def test_disallowed_sql(query, needle):
    errors = check_query(query, VIEWS)
    assert any(needle in e for e in errors), errors


@pytest.mark.parametrize(
    "query",
    [
        "SELECT a, b FROM t1",
        "SELECT t1.a, t2.b FROM t1 JOIN t2 ON t1.id = t2.id",
        "SELECT a FROM t1 UNION ALL SELECT a FROM t2",
        "WITH c AS (SELECT a, count(*) AS n FROM t1 GROUP BY a) SELECT * FROM c WHERE n > 1",
        "SELECT sha256(CAST(a AS VARCHAR)) AS h, coalesce(b, 0) AS b FROM t1",
        "SELECT a, row_number() OVER (PARTITION BY b ORDER BY a) AS rn FROM t1",
        "SELECT * EXCLUDE (b) FROM t1 LEFT JOIN t2 USING (id)",
    ],
)
def test_allowed_sql(query):
    assert check_query(query, VIEWS) == []


def test_duckdb_runtime_lockdown_blocks_io_even_without_static_policy():
    duckdb = pytest.importorskip("duckdb")
    pa = pytest.importorskip("pyarrow")
    from depa_train.plugins.sql_duckdb import SqlConfig
    from depa_train.plugins.sql_duckdb.impl import _connect

    cfg = SqlConfig.model_validate(
        {"tables": [{"name": "t", "input": "a", "format": "csv"}], "query": "SELECT 1"}
    )
    con = _connect(cfg)
    con.register("t", pa.table({"a": [1, 2]}))
    assert con.sql("SELECT sum(a) FROM t").fetchall() == [(3,)]
    for attack in (
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM '/etc/passwd'",
        "COPY t TO '/tmp/depa-exfil.csv'",
        "ATTACH '/tmp/depa-x.db'",
        "INSTALL httpfs",
        "LOAD httpfs",
        "SELECT * FROM read_parquet('https://example.com/x.parquet')",
        "SET enable_external_access = true",
        "SET lock_configuration = false",
    ):
        with pytest.raises(duckdb.Error):
            con.execute(attack)
