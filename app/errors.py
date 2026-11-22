"""业务错误类型。"""
from __future__ import annotations


class AppError(Exception):
    """所有可预期的业务/输入错误基类。"""

    status_code = 400

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code


class CsvError(AppError):
    """CSV 解析或行级校验失败。"""


class StaleVersionError(AppError):
    """提交依据的版号已过期（并发冲突）。"""

    status_code = 409


class NoCommonSpecError(AppError):
    """相邻两月无共有规格，拒绝换版。"""

    status_code = 422
