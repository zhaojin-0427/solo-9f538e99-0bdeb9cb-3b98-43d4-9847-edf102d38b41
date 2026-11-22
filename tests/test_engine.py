"""指数引擎纯函数测试：环比、连乘、无共有规格、更正作用域。"""
from decimal import Decimal

import pytest

from app.engine import (BASE_INDEX, build_indices, common_specs,
                        recompute_correction, ratio_between)
from app.errors import NoCommonSpecError

from conftest import q


def test_common_specs_only_shared():
    prev = [q("A", 1), q("B", 2)]
    cur = [q("B", 3), q("C", 4)]
    assert common_specs(prev, cur) == ["B"]


def test_ratio_uses_only_common_specs():
    prev = [q("A", 100), q("B", 200), q("X", 999)]
    cur = [q("A", 110), q("B", 220), q("Y", 1)]
    r = ratio_between(prev, cur, "2026-01", "2026-02")
    # 环比 = (110+220)/(100+200) = 1.1，X/Y 不参与
    assert r["ratio"] == Decimal("330") / Decimal("300")
    assert r["common_count"] == 2
    assert r["common_specs"] == ["A", "B"]


def test_no_common_specs_raises_with_month_names():
    with pytest.raises(NoCommonSpecError) as ei:
        ratio_between([q("A", 1)], [q("B", 2)], "2026-01", "2026-02")
    assert "2026-01" in ei.value.message and "2026-02" in ei.value.message


def test_chain_index_first_month_100(jan_rows, feb_rows, mar_rows):
    quotes = {"2026-01": jan_rows, "2026-02": feb_rows, "2026-03": mar_rows}
    stats = build_indices(quotes)
    assert stats[0]["index"] == BASE_INDEX
    # 1->2 月：共有 A,B，(110+220)/(100+200)=1.1
    assert stats[1]["ratio"] == Decimal("1.1")
    assert stats[1]["index"] == Decimal("110.0")
    # 2->3 月：共有 A,B,D，(121+242+88)/(110+220+80)=451/410
    r23 = Decimal("451") / Decimal("410")
    assert stats[2]["ratio"] == r23
    assert stats[2]["index"] == Decimal("110.0") * r23
    assert stats[1]["common_count"] == 2
    assert stats[2]["common_count"] == 3


def _full(quotes):
    return {s["month"]: s for s in build_indices(quotes)}


def test_correction_matches_full_recompute_and_shifts_later_indices(jan_rows, feb_rows, mar_rows):
    """更正 2 月：scoped 重算结果必须与全量重算完全一致，且 3 月指数被带动更新。"""
    quotes = {"2026-01": jan_rows, "2026-02": feb_rows, "2026-03": mar_rows}
    old_stats = build_indices(quotes)
    old_index_3 = old_stats[2]["index"]

    # 更正 2 月报价（只换 B 的单价）
    corrected_feb = [q("A", 110), q("B", 260), q("D", 80)]
    new_quotes = dict(quotes)
    new_quotes["2026-02"] = corrected_feb

    scoped = recompute_correction(old_stats, quotes, new_quotes, "2026-02")
    full = build_indices(new_quotes)

    assert len(scoped) == len(full)
    for got, want in zip(scoped, full):
        assert got["month"] == want["month"]
        assert got["index"] == want["index"]
        assert got["ratio"] == want["ratio"]
        assert got["common_count"] == want["common_count"]
        assert got["common_specs"] == want["common_specs"]

    by_month = {s["month"]: s for s in scoped}
    # 2 月环比变化 -> 2 月指数变化
    assert by_month["2026-02"]["index"] != Decimal("110.0")
    # 更正 2 月 B 单价后，2->3 月环比的分母含 B，故环比本身也会变；
    # 但 3 月的报价行始终未被读取/修改（作用域语义），且 scoped 与全量
    # 重算结果逐字段相等（上面的循环已断言），3 月指数随之正确连乘更新。
    assert by_month["2026-03"]["index"] != old_index_3
    assert by_month["2026-03"]["index"] == (
        by_month["2026-02"]["index"] * by_month["2026-03"]["ratio"]
    )
    # 作用域外的“报价明细”未被触碰
    assert new_quotes["2026-03"] == quotes["2026-03"]
    assert new_quotes["2026-01"] == quotes["2026-01"]


def test_correction_first_month_only(jan_rows, feb_rows):
    quotes = {"2026-01": jan_rows, "2026-02": feb_rows}
    old_stats = build_indices(quotes)
    new_jan = [q("A", 120), q("B", 200), q("C", 50)]
    new_quotes = dict(quotes)
    new_quotes["2026-01"] = new_jan
    scoped = recompute_correction(old_stats, quotes, new_quotes, "2026-01")
    full = build_indices(new_quotes)
    for got, want in zip(scoped, full):
        assert got["index"] == want["index"]
        assert got["ratio"] == want["ratio"]


def test_correction_rejects_when_no_common_specs(jan_rows, feb_rows, mar_rows):
    quotes = {"2026-01": jan_rows, "2026-02": feb_rows, "2026-03": mar_rows}
    old_stats = build_indices(quotes)
    # 把 2 月改成与 1 月完全不同的规格 -> (1月,2月) 无共有规格
    bad_feb = [q("X", 1), q("Y", 2)]
    new_quotes = dict(quotes)
    new_quotes["2026-02"] = bad_feb
    with pytest.raises(NoCommonSpecError) as ei:
        recompute_correction(old_stats, quotes, new_quotes, "2026-02")
    assert "2026-01" in ei.value.message and "2026-02" in ei.value.message
