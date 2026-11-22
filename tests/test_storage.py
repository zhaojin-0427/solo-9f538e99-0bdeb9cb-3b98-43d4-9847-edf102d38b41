"""存储层：换版流程、版本快照、更正持久化、并发冲突。"""
import pytest

from app import storage
from app.engine import build_indices
from app.errors import AppError, NoCommonSpecError, StaleVersionError
from app.months import next_month

from conftest import q, publish_via_storage


def test_first_append_correction_flow(db_path, jan_rows, feb_rows, mar_rows):
    r1 = publish_via_storage(db_path, "2026-01", jan_rows)
    assert r1["version_id"] == 1 and r1["kind"] == "first"
    r2 = publish_via_storage(db_path, "2026-02", feb_rows, base_version=1)
    assert r2["version_id"] == 2 and r2["kind"] == "append"
    r3 = publish_via_storage(db_path, "2026-03", mar_rows, base_version=2)
    assert r3["version_id"] == 3

    # 整月更正 2 月
    corrected = [q("A", 110), q("B", 260), q("D", 80)]
    r4 = publish_via_storage(db_path, "2026-02", corrected, base_version=3)
    assert r4["kind"] == "correction" and r4["version_id"] == 4

    # 新版本快照中 2 月报价已整体替换，1、3 月报价原样保留
    q4 = storage.load_quotes(storage.connect(db_path), 4)
    assert q4["2026-02"] == corrected
    assert q4["2026-01"] == jan_rows
    assert q4["2026-03"] == mar_rows

    # 旧版快照未被覆盖：v3 的 2 月仍是旧报价
    conn = storage.connect(db_path)
    q3 = storage.load_quotes(conn, 3)
    assert q3["2026-02"] == feb_rows
    conn.close()


def test_old_versions_remain_queryable(db_path, jan_rows, feb_rows, mar_rows):
    publish_via_storage(db_path, "2026-01", jan_rows)
    publish_via_storage(db_path, "2026-02", feb_rows, base_version=1)
    publish_via_storage(db_path, "2026-03", mar_rows, base_version=2)
    conn = storage.connect(db_path)
    s1 = storage.load_stats(conn, 1)
    s2 = storage.load_stats(conn, 2)
    s3 = storage.load_stats(conn, 3)
    assert [x["month"] for x in s1] == ["2026-01"]
    assert [x["month"] for x in s2] == ["2026-01", "2026-02"]
    assert [x["month"] for x in s3] == ["2026-01", "2026-02", "2026-03"]
    assert s1[0]["index"].__str__() == "100"
    versions = storage.list_versions(conn)
    assert [v["version_id"] for v in versions] == [1, 2, 3]
    conn.close()


def test_append_must_be_immediate_next_month(db_path, jan_rows):
    publish_via_storage(db_path, "2026-01", jan_rows)
    with pytest.raises(AppError) as ei:
        publish_via_storage(db_path, "2026-03", [q("A", 1)], base_version=1)
    assert "2026-02" in ei.value.message
    assert next_month("2026-01") == "2026-02"


def test_stale_base_version_rejected(db_path, jan_rows, feb_rows):
    publish_via_storage(db_path, "2026-01", jan_rows)
    # 另一提交先把版本推进到 v2
    publish_via_storage(db_path, "2026-02", feb_rows, base_version=1)
    # 仍拿着过期的 base_version=1 提交 -> 409，提示重试，且不产生新版
    with pytest.raises(StaleVersionError) as ei:
        publish_via_storage(db_path, "2026-02", [q("A", 999)], base_version=1)
    assert "v1" in ei.value.message and "v2" in ei.value.message and "重试" in ei.value.message
    conn = storage.connect(db_path)
    assert storage.latest_version_id(conn) == 2
    conn.close()


def test_no_common_specs_aborts_version(db_path, jan_rows, feb_rows):
    publish_via_storage(db_path, "2026-01", jan_rows)
    with pytest.raises(NoCommonSpecError):
        publish_via_storage(db_path, "2026-02", [q("X", 1), q("Y", 2)], base_version=1)
    conn = storage.connect(db_path)
    # 被拒绝后没有新版本，最新版仍是 v1
    assert storage.latest_version_id(conn) == 1
    assert [v["version_id"] for v in storage.list_versions(conn)] == [1]
    conn.close()


def test_invalid_month_rejected(db_path, jan_rows):
    with pytest.raises((AppError, ValueError)):
        publish_via_storage(db_path, "2026/01", jan_rows)


def test_concurrent_publishers_one_wins_one_gets_stale(db_path, jan_rows, feb_rows, mar_rows):
    """两个连接并发提交：写锁串行化保证不产生脏版本；持过期 base_version 者收到 409。"""
    import threading

    # 先发布首月 v1
    publish_via_storage(db_path, "2026-01", jan_rows)

    results: list[Exception | dict] = []
    barrier = threading.Barrier(2)

    def worker(month, rows, base):
        conn = storage.connect(db_path)
        barrier.wait()  # 尽量让两个事务同时开始
        try:
            results.append(storage.publish(conn, month, rows, base))
        except Exception as exc:  # noqa: BLE001 - 测试需要收集所有结果
            results.append(exc)
        finally:
            conn.close()

    t_ok = threading.Thread(target=worker, args=("2026-02", feb_rows, 1))
    t_stale = threading.Thread(target=worker, args=("2026-02", mar_rows, 1))
    t_ok.start()
    t_stale.start()
    t_ok.join()
    t_stale.join()

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]
    assert len(successes) == 1 and len(failures) == 1
    assert isinstance(failures[0], StaleVersionError)
    # 实际发布顺序：只生成了一个新版本 v2，且报价来自抢到锁的成功方
    conn = storage.connect(db_path)
    assert storage.latest_version_id(conn) == 2
    stored = storage.load_quotes(conn, 2)["2026-02"]
    assert stored in (feb_rows, mar_rows)
    conn.close()
