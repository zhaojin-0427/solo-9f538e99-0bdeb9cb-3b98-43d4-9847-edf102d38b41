"""共享 fixtures：临时数据库 + Flask 测试客户端 + 示例报价。"""
from __future__ import annotations

import os
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app import storage  # noqa: E402


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture()
def app(db_path):
    app = create_app(db_path=db_path)
    app.config.update(TESTING=True)
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def q(spec, price):
    return {"spec_code": spec, "unit_price": Decimal(str(price))}


@pytest.fixture()
def jan_rows():
    return [q("A", 100), q("B", 200), q("C", 50)]


@pytest.fixture()
def feb_rows():
    # A,B 共有；D 新增；C 退出
    return [q("A", 110), q("B", 220), q("D", 80)]


@pytest.fixture()
def mar_rows():
    return [q("A", 121), q("B", 242), q("D", 88)]


def publish_via_storage(db_path, month, rows, base_version=None):
    conn = storage.connect(db_path)
    storage.init_db(conn)
    try:
        return storage.publish(conn, month, rows, base_version)
    finally:
        conn.close()
