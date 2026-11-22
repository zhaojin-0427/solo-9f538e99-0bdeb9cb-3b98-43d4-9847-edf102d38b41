"""CSV 解析与行级校验。"""
from decimal import Decimal

import pytest

from app.csvparse import parse_quotes_csv
from app.errors import CsvError


def test_parse_ok_with_bom_and_extra_columns():
    raw = (
        "﻿spec_code,unit_price,remark\n"
        "A,100.5,水泥\n"
        "B, 200 ,\n"
    )
    rows = parse_quotes_csv(raw.encode("utf-8"))
    assert rows == [
        {"spec_code": "A", "unit_price": Decimal("100.5")},
        {"spec_code": "B", "unit_price": Decimal("200")},
    ]


def test_missing_header_rejected():
    with pytest.raises(CsvError):
        parse_quotes_csv("code,price\nA,1\n")


def test_empty_file_rejected():
    with pytest.raises(CsvError):
        parse_quotes_csv("")


def test_no_data_rows_rejected():
    with pytest.raises(CsvError):
        parse_quotes_csv("spec_code,unit_price\n\n")


def test_duplicate_spec_rejected_with_line_number():
    raw = "spec_code,unit_price\nA,1\nA,2\n"
    with pytest.raises(CsvError) as ei:
        parse_quotes_csv(raw)
    assert "第 3 行" in ei.value.message


def test_non_positive_price_rejected():
    for bad in ("0", "-5", "abc"):
        with pytest.raises(CsvError):
            parse_quotes_csv(f"spec_code,unit_price\nA,{bad}\n")


def test_blank_spec_rejected():
    with pytest.raises(CsvError):
        parse_quotes_csv("spec_code,unit_price\n ,10\n")


def test_comment_lines_before_header_skipped():
    raw = "# 这是注释\nspec_code,unit_price\nA,10\n"
    rows = parse_quotes_csv(raw)
    assert rows == [{"spec_code": "A", "unit_price": Decimal("10")}]
