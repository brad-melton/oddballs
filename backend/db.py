"""
Central DB connection for the backend.

Local dev (no TURSO_DATABASE_URL set) keeps using a plain sqlite3 file
connection -- completely unchanged from before. When TURSO_DATABASE_URL /
TURSO_AUTH_TOKEN are set (as they will be on Render), connects to the
hosted Turso database instead, via SQLAlchemy's libsql dialect. The rest
of the codebase doesn't need to know which one it's talking to: both a
plain sqlite3.Row and this module's wrapped rows support row["column"]
access the same way.

sqlalchemy-libsql (and the libsql-experimental package it depends on) only
ships prebuilt wheels for Linux/macOS -- there's no Windows build, ARM64 or
otherwise, and building it from source needs a Rust toolchain this machine
doesn't have (confirmed: pip install fails trying to compile it via
maturin/cargo). Its import is deferred into the Turso-only branch below so
local Windows dev never needs it installed at all.
"""
import os
import sqlite3

TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN")

LOCAL_DB_PATH = os.environ.get("DB_PATH", r"C:/Users/bsmel/OneDrive/Documents/Baseball_data/10u data.db")

_engine = None


def _turso_sqlalchemy_url() -> str:
    # sqlalchemy-libsql wants "sqlite+libsql://<host>/?secure=true", not the
    # "libsql://<host>" form Turso's own dashboard/CLI show.
    host = TURSO_DATABASE_URL.replace("libsql://", "").replace("https://", "")
    return f"sqlite+libsql://{host}?secure=true"


def _get_engine():
    global _engine
    if _engine is None:
        from sqlalchemy import create_engine  # deferred -- see module docstring
        _engine = create_engine(_turso_sqlalchemy_url(), connect_args={"auth_token": TURSO_AUTH_TOKEN})
    return _engine


class _CompatCursor:
    """Wraps a SQLAlchemy CursorResult so .fetchall()/.fetchone() return
    dict-like rows (row["col"]), matching sqlite3.Row's behavior."""

    def __init__(self, result):
        self._result = result

    def fetchall(self):
        return self._result.mappings().all()

    def fetchone(self):
        return self._result.mappings().first()

    @property
    def rowcount(self):
        return self._result.rowcount


class _CompatConnection:
    """Adapts a SQLAlchemy Connection to the sqlite3.Connection surface
    this codebase already uses everywhere: .execute(sql, params),
    .commit(), .close(). exec_driver_sql passes params straight through in
    the underlying driver's native '?' qmark style, so none of the existing
    SQL strings need to change."""

    def __init__(self, sa_conn):
        self._conn = sa_conn

    def execute(self, sql, params=()):
        return _CompatCursor(self._conn.exec_driver_sql(sql, params))

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def get_connection():
    """
    Returns a connection usable exactly like sqlite3.connect(...) with
    row_factory=sqlite3.Row: conn.execute(sql, params).fetchall() /
    .fetchone() give rows indexable by column name, conn.commit() /
    .close() work as normal. Backed by Turso when TURSO_DATABASE_URL is
    set, otherwise the local sqlite file.
    """
    if TURSO_DATABASE_URL:
        return _CompatConnection(_get_engine().connect())

    conn = sqlite3.connect(LOCAL_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
