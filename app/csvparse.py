"""报价 CSV 解析与行级校验。

文件编码支持 UTF-8（可带 BOM），首行为表头，至少包含两列：
    spec_code, unit_price
多余的列会被忽略。规格码同一文件内必须唯一；单价必须为正数。
"""
from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation

from .errors import CsvError


def parse_quotes_csv(raw: bytes | str) -> list[dict]:
    """解析 CSV 为 [{"spec_code": str, "unit_price": Decimal}, ...]。

    - 空文件/无数据行 -> CsvError
    - 规格码为空、重复 -> CsvError（带行号）
    - 单价无法解析、非正数 -> CsvError（带行号）
    """
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise CsvError(f"CSV 编码不是 UTF-8：{exc}") from exc
    else:
        text = raw

    reader = csv.reader(io.StringIO(text))
    header = None
    for raw_row in reader:
        # 允许 # 开头的注释行与空行出现在表头之前
        if raw_row and str(raw_row[0]).strip().startswith("#"):
            continue
        if not raw_row or all(not str(c).strip() for c in raw_row):
            continue
        header = raw_row
        break
    if header is None:
        raise CsvError("CSV 为空，缺少表头与数据行")

    headers = [h.strip().lower() for h in header]
    try:
        code_idx = headers.index("spec_code")
        price_idx = headers.index("unit_price")
    except ValueError as exc:
        raise CsvError(
            "CSV 表头必须包含 spec_code 和 unit_price 两列，"
            f"实际表头：{header}"
        ) from exc

    rows: list[dict] = []
    seen: set[str] = set()
    # 表头是第 1 行，第一条数据从第 2 行开始
    for line_no, row in enumerate(reader, start=2):
        if not row or all(not str(c).strip() for c in row):
            continue  # 跳过空行
        if len(row) <= max(code_idx, price_idx):
            raise CsvError(f"第 {line_no} 行列数不足，缺少规格码或单价")
        spec_code = row[code_idx].strip()
        price_raw = row[price_idx].strip()
        if not spec_code:
            raise CsvError(f"第 {line_no} 行规格码为空")
        if spec_code in seen:
            raise CsvError(f"第 {line_no} 行规格码 {spec_code} 在文件内重复，规格码必须唯一")
        try:
            price = Decimal(price_raw)
        except InvalidOperation as exc:
            raise CsvError(f"第 {line_no} 行单价 {price_raw!r} 不是合法数字") from exc
        if not price.is_finite():
            raise CsvError(f"第 {line_no} 行单价必须为有限数字")
        if price <= 0:
            raise CsvError(f"第 {line_no} 行单价 {price_raw} 不是正数，单价必须大于 0")
        seen.add(spec_code)
        rows.append({"spec_code": spec_code, "unit_price": price})

    if not rows:
        raise CsvError("CSV 没有任何有效数据行")
    return rows
