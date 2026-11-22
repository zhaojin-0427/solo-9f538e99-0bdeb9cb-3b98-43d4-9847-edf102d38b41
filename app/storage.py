"""SQLite 存储层：版本化快照。

每次成功换版都会生成新的 version_id，并把当时的全部报价明细
与逐月统计（指数、环比、共有规格数）整体快照保存，因此任意旧版
都可以按版号完整查询。

并发控制：
- 写事务一律 BEGIN IMMEDIATE，跨进程由 SQLite 文件锁串行化，
  实际发布顺序即拿到写锁的顺序；
- 提交时携带 base_version（客户端看到的最新版号），若库内最新版
  已推进则抛 StaleVersionError(409) 提示重试；
- busy_timeout 让后到的提交等待锁而不是立刻失败。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

from .engine import build_indices, recompute_correction
from .errors import AppError, StaleVersionError
from .months import month_index, next_month, validate_month

SCHEMA = """
CREATE TABLE IF NOT EXISTS versions (
    version_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_version INTEGER,
    kind           TEXT NOT NULL CHECK (kind IN ('first', 'append', 'correction')),
    target_month   TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quotes (
    version_id INTEGER NOT NULL,
    month      TEXT NOT NULL,
    spec_code  TEXT NOT NULL,
    unit_price TEXT NOT NULL,
    PRIMARY KEY (version_id, month, spec_code)
);
CREATE TABLE IF NOT EXISTS month_stats (
    version_id       INTEGER NOT NULL,
    month            TEXT NOT NULL,
    prev_month       TEXT,
    ratio            TEXT,
    index_value      TEXT NOT NULL,
    common_count     INTEGER NOT NULL,
    common_specs     TEXT NOT NULL,
    sum_prev_common  TEXT,
    sum_cur_common   TEXT,
    PRIMARY KEY (version_id, month)
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# ---------- 读取 ----------

def latest_version_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(version_id) AS v FROM versions").fetchone()
    return row["v"]


def list_versions(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT version_id, parent_version, kind, target_month, created_at "
        "FROM versions ORDER BY version_id"
    ).fetchall()]


def load_quotes(conn: sqlite3.Connection, version_id: int) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in conn.execute(
        "SELECT month, spec_code, unit_price FROM quotes "
        "WHERE version_id = ? ORDER BY month, spec_code", (version_id,)
    ):
        out.setdefault(r["month"], []).append(
            {"spec_code": r["spec_code"], "unit_price": Decimal(r["unit_price"])}
        )
    return out


def load_stats(conn: sqlite3.Connection, version_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT month, prev_month, ratio, index_value, common_count, "
        "common_specs, sum_prev_common, sum_cur_common "
        "FROM month_stats WHERE version_id = ? ORDER BY month", (version_id,)
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "month": r["month"],
            "prev_month": r["prev_month"],
            "ratio": Decimal(r["ratio"]) if r["ratio"] is not None else None,
            "index": Decimal(r["index_value"]),
            "common_count": r["common_count"],
            "common_specs": json.loads(r["common_specs"]),
            "sum_prev_common": Decimal(r["sum_prev_common"])
            if r["sum_prev_common"] is not None else None,
            "sum_cur_common": Decimal(r["sum_cur_common"])
            if r["sum_cur_common"] is not None else None,
        })
    return out


# ---------- 写入 ----------

def _snapshot(conn: sqlite3.Connection, version_id: int,
              quotes_by_month: dict[str, list[dict]], stats: list[dict]) -> None:
    conn.executemany(
        "INSERT INTO quotes(version_id, month, spec_code, unit_price) "
        "VALUES (?, ?, ?, ?)",
        [(version_id, m, q["spec_code"], str(q["unit_price"]))
         for m, rows in sorted(quotes_by_month.items(), key=lambda kv: month_index(kv[0]))
         for q in rows]
    )
    conn.executemany(
        "INSERT INTO month_stats(version_id, month, prev_month, ratio, index_value, "
        "common_count, common_specs, sum_prev_common, sum_cur_common) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(version_id, s["month"], s["prev_month"],
          str(s["ratio"]) if s["ratio"] is not None else None,
          str(s["index"]), s["common_count"], json.dumps(s["common_specs"]),
          str(s["sum_prev_common"]) if s["sum_prev_common"] is not None else None,
          str(s["sum_cur_common"]) if s["sum_cur_common"] is not None else None)
         for s in stats]
    )


def publish(conn: sqlite3.Connection, month: str, rows: list[dict],
            base_version: int | None, note: str | None = None) -> dict:
    """一次发布（换版）。返回新版本摘要。

    month 不在已有月份中 -> 首月或续报下月；
    month 已存在 -> 整月更正（报价整体替换该月）。
    """
    month = validate_month(month)
    if not rows:
        raise AppError("报价数据为空，拒绝换版")

    conn.execute("BEGIN IMMEDIATE")
    try:
        current = latest_version_id(conn)
        if base_version is not None and current != base_version:
            raise StaleVersionError(
                f"提交依据的版号 v{base_version} 已过期，当前最新版为 "
                f"v{current}，请刷新查看最新数据后重试提交"
            )

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if current is None:
            kind = "first"
            new_quotes = {month: rows}
            stats = build_indices(new_quotes)
            parent = None
        else:
            old_quotes = load_quotes(conn, current)
            old_stats = load_stats(conn, current)
            if month in old_quotes:
                # 整月更正
                kind = "correction"
                new_quotes = dict(old_quotes)
                new_quotes[month] = rows  # 只替换所改月份
                stats = recompute_correction(old_stats, old_quotes, new_quotes, month)
                parent = current
            else:
                # 续报：必须紧接当前最后一个月
                last_month = max(old_quotes, key=month_index)
                expected = next_month(last_month)
                if month != expected:
                    raise AppError(
                        f"当前已发布到 {last_month}，新月必须为其下一月 {expected}，"
                        f"收到 {month}；如需修改已发布月份请使用整月更正"
                    )
                kind = "append"
                new_quotes = dict(old_quotes)
                new_quotes[month] = rows
                stats = build_indices(new_quotes)
                parent = current

        cur = conn.execute(
            "INSERT INTO versions(parent_version, kind, target_month, created_at) "
            "VALUES (?, ?, ?, ?)", (parent, kind, month, now)
        )
        new_id = cur.lastrowid
        _snapshot(conn, new_id, new_quotes, stats)
        conn.commit()
        return {
            "version_id": new_id,
            "kind": kind,
            "target_month": month,
            "created_at": now,
            "stats": stats,
        }
    except Exception:
        conn.rollback()
        raise
