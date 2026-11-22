"""月份工具：内部统一使用 YYYY-MM 字符串，按自然月增减。"""
from __future__ import annotations

import re

_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def validate_month(month: str) -> str:
    """校验月份格式，非法时抛 ValueError。"""
    if not isinstance(month, str) or not _MONTH_RE.match(month.strip()):
        raise ValueError(f"月份格式应为 YYYY-MM，收到：{month!r}")
    return month.strip()


def shift_month(month: str, delta: int) -> str:
    """月份加减 delta 个月。"""
    y, m = map(int, validate_month(month).split("-"))
    total = y * 12 + (m - 1) + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def next_month(month: str) -> str:
    return shift_month(month, 1)


def prev_month(month: str) -> str:
    return shift_month(month, -1)


def month_index(month: str) -> int:
    """可用于排序/比较的整数序号。"""
    y, m = map(int, validate_month(month).split("-"))
    return y * 12 + (m - 1)
