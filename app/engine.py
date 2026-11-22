"""价格指数计算引擎（纯函数，便于单测）。

规则：
- 首月指数 = 100。
- 相邻两月仅比较共有规格（同一 spec_code）。
- 环比 = 本月共有规格单价合计 / 上月共有规格单价合计。
- 指数 = 上月指数 × 环比，逐月连乘。
- 相邻两月无共有规格 -> NoCommonSpecError，指出具体月份并拒绝换版。

更正时的作用域（满足“只读所改月份及前后各一个月的报价明细”）：
修改月 M 只会改变 (M-1, M) 与 (M, M+1) 两个环比；M+1 之后的环比
不依赖 M 的报价，沿用旧版已发布的环比即可。因此 scoped 重算只会
读取 M-1、M、M+1 三个月的报价明细。
"""
from __future__ import annotations

from decimal import Decimal, getcontext

from .errors import NoCommonSpecError
from .months import month_index, next_month, prev_month

getcontext().prec = 28

BASE_INDEX = Decimal("100")


def _rows_to_map(rows: list[dict]) -> dict[str, Decimal]:
    return {r["spec_code"]: r["unit_price"] for r in rows}


def common_specs(prev_rows: list[dict], cur_rows: list[dict]) -> list[str]:
    """两月共有规格码，按规格码排序，保证结果稳定。"""
    return sorted(set(_rows_to_map(prev_rows)) & set(_rows_to_map(cur_rows)))


def ratio_between(prev_rows: list[dict], cur_rows: list[dict],
                  prev_month: str, cur_month: str) -> dict:
    """计算一对相邻月份的环比及共有规格统计。"""
    prev_map = _rows_to_map(prev_rows)
    cur_map = _rows_to_map(cur_rows)
    common = sorted(set(prev_map) & set(cur_map))
    if not common:
        raise NoCommonSpecError(
            f"{prev_month} 与 {cur_month} 没有任何共有规格，无法计算环比，本次换版被拒绝"
        )
    sum_prev = sum((prev_map[c] for c in common), Decimal("0"))
    sum_cur = sum((cur_map[c] for c in common), Decimal("0"))
    return {
        "prev_month": prev_month,
        "cur_month": cur_month,
        "common_count": len(common),
        "common_specs": common,
        "sum_prev_common": sum_prev,
        "sum_cur_common": sum_cur,
        "ratio": sum_cur / sum_prev,
    }


def build_indices(quotes_by_month: dict[str, list[dict]]) -> list[dict]:
    """对一整套月份报价做全量计算，返回按月排序的统计行。"""
    months = sorted(quotes_by_month, key=month_index)
    stats: list[dict] = []
    running = BASE_INDEX
    for i, month in enumerate(months):
        if i == 0:
            stats.append({
                "month": month,
                "prev_month": None,
                "index": BASE_INDEX,
                "ratio": None,
                "common_count": 0,
                "common_specs": [],
                "sum_prev_common": None,
                "sum_cur_common": None,
            })
            continue
        pm = months[i - 1]
        pair = ratio_between(quotes_by_month[pm], quotes_by_month[month], pm, month)
        running = running * pair["ratio"]
        stats.append({
            "month": month,
            "prev_month": pm,
            "index": running,
            "ratio": pair["ratio"],
            "common_count": pair["common_count"],
            "common_specs": pair["common_specs"],
            "sum_prev_common": pair["sum_prev_common"],
            "sum_cur_common": pair["sum_cur_common"],
        })
    return stats


def recompute_correction(old_stats: list[dict],
                         old_quotes: dict[str, list[dict]],
                         new_quotes: dict[str, list[dict]],
                         changed_month: str) -> list[dict]:
    """整月更正后的作用域内重算。

    只读取 changed_month 及其前一月、后一月（存在者）的报价明细；
    更靠后的月份环比直接沿用旧版统计，仅重算指数连乘。
    """
    stats_by_month = {s["month"]: s for s in old_stats}
    months = sorted(set(stats_by_month) | set(new_quotes), key=month_index)

    pm = prev_month(changed_month)
    nm = next_month(changed_month)
    readable = {changed_month}
    if pm in stats_by_month:
        readable.add(pm)
    if nm in stats_by_month:
        readable.add(nm)
    # 防御性断言：本函数只允许触碰这三个月的报价明细
    for m in months:
        if m not in readable:
            assert new_quotes[m] == old_quotes[m], f"更正作用域外的月份 {m} 报价被改动"

    # 找重算起点：受影响的最早月份（changed 的前一月，若存在）
    start = pm if pm in stats_by_month else changed_month
    start_idx = months.index(start)

    # 起点之前的统计原样保留；起点的锚点指数取前一月（或首月 100）
    out = [dict(s) for s in old_stats if month_index(s["month"]) < month_index(start)]
    anchor = out[-1]["index"] if out else None

    result_by_month: dict[str, dict] = {s["month"]: dict(s) for s in out}
    running = anchor
    for idx, month in enumerate(months[start_idx:], start=start_idx):
        if idx == 0:
            entry = {
                "month": month,
                "prev_month": None,
                "index": BASE_INDEX,
                "ratio": None,
                "common_count": 0,
                "common_specs": [],
                "sum_prev_common": None,
                "sum_cur_common": None,
            }
        else:
            pmonth = months[idx - 1]
            # 只要这对相邻月份的报价在读取作用域内，就重新计算环比；
            # 作用域（M-1、M、M+1）之外的相邻对环比不受更正影响，沿用旧版
            if pmonth in readable and month in readable:
                pair = ratio_between(
                    new_quotes[pmonth], new_quotes[month], pmonth, month
                )
                ratio = pair["ratio"]
                common_count = pair["common_count"]
                common_specs = pair["common_specs"]
                sum_prev = pair["sum_prev_common"]
                sum_cur = pair["sum_cur_common"]
            else:
                # 作用域外：环比未变，沿用旧版已发布统计
                old = stats_by_month[month]
                ratio = old["ratio"]
                common_count = old["common_count"]
                common_specs = list(old["common_specs"])
                sum_prev = old["sum_prev_common"]
                sum_cur = old["sum_cur_common"]
            running = running * ratio
            entry = {
                "month": month,
                "prev_month": pmonth,
                "index": running,
                "ratio": ratio,
                "common_count": common_count,
                "common_specs": common_specs,
                "sum_prev_common": sum_prev,
                "sum_cur_common": sum_cur,
            }
        running = entry["index"]
        result_by_month[month] = entry

    return [result_by_month[m] for m in months]
