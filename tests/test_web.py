"""HTTP 端到端：页面发布、CSV 校验、409、无共有规格拒绝、旧版查询、JSON API。"""
import io

from app import storage

from conftest import q


def _upload(client, month, rows_csv, base_version=None):
    data = {
        "month": month,
        "csv_file": (io.BytesIO(rows_csv.encode("utf-8")), "q.csv"),
    }
    if base_version is not None:
        data["base_version"] = str(base_version)
    return client.post("/publish", data=data, content_type="multipart/form-data",
                       follow_redirects=True)


JAN = "spec_code,unit_price\nA,100\nB,200\nC,50\n"
FEB = "spec_code,unit_price\nA,110\nB,220\nD,80\n"


def test_full_web_flow_and_history(client, db_path):
    r = _upload(client, "2026-01", JAN)
    assert r.status_code == 200 and "发布成功" in r.get_data(as_text=True)
    r = _upload(client, "2026-02", FEB, base_version=1)
    assert r.status_code == 200

    # 首页展示指数与共有规格数
    html = r.get_data(as_text=True)
    assert "2026-01" in html and "2026-02" in html and "110.00" in html

    # 旧版 v1 仍可查询，只有一个月
    r = client.get("/versions/1")
    assert r.status_code == 200
    assert "2026-02" not in r.get_data(as_text=True)

    # 报价明细页标注共有规格
    r = client.get("/versions/2/months/2026-02/quotes")
    text = r.get_data(as_text=True)
    assert "A" in text and "非共有规格" in text  # D 不参与环比

    # 不存在的版号/月份 -> 404
    assert client.get("/versions/99").status_code == 404
    assert client.get("/versions/2/months/2026-09/quotes").status_code == 404


def test_bad_csv_flashes_error_and_no_version(client, app):
    r = _upload(client, "2026-01", "spec_code,unit_price\nA,-3\n")
    assert "正数" in r.get_data(as_text=True)
    conn = storage.connect(app.config["DB_PATH"])
    assert storage.latest_version_id(conn) is None
    conn.close()


def test_missing_file_rejected(client):
    r = client.post("/publish", data={"month": "2026-01"},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "CSV" in r.get_data(as_text=True)


def test_no_common_specs_rejected_via_web(client):
    _upload(client, "2026-01", JAN)
    r = _upload(client, "2026-02", "spec_code,unit_price\nX,1\nY,2\n", base_version=1)
    text = r.get_data(as_text=True)
    assert "没有任何共有规格" in text and "2026-01" in text and "2026-02" in text
    conn = storage.connect(client.application.config["DB_PATH"])
    assert storage.latest_version_id(conn) == 1
    conn.close()


def test_stale_submission_via_api(client):
    # 首月
    r = client.post("/api/publish", data={
        "month": "2026-01",
        "csv_file": (io.BytesIO(JAN.encode()), "a.csv"),
    }, content_type="multipart/form-data")
    assert r.status_code == 201 and r.get_json()["version_id"] == 1

    # 先推进到 v2
    client.post("/api/publish", data={
        "month": "2026-02", "base_version": "1",
        "csv_file": (io.BytesIO(FEB.encode()), "b.csv"),
    }, content_type="multipart/form-data")

    # 持过期 base_version=1 再更正
    r = client.post("/api/publish", data={
        "month": "2026-02", "base_version": "1",
        "csv_file": (io.BytesIO(FEB.encode()), "c.csv"),
    }, content_type="multipart/form-data")
    assert r.status_code == 409
    body = r.get_json()
    assert body["error"] == "StaleVersionError"
    assert "最新版号" in body["retry"] and "base_version" in body["retry"]


def test_api_version_listing_and_detail(client):
    client.post("/api/publish", data={
        "month": "2026-01",
        "csv_file": (io.BytesIO(JAN.encode()), "a.csv"),
    }, content_type="multipart/form-data")
    r = client.get("/api/versions")
    assert r.status_code == 200 and r.get_json()["latest_version"] == 1
    r = client.get("/api/versions/1")
    body = r.get_json()
    assert body["months"][0]["month"] == "2026-01"
    assert body["months"][0]["index"] == "100"
    assert body["quote_counts"]["2026-01"] == 3
    assert client.get("/api/versions/77").status_code == 404
    assert client.get("/healthz").status_code == 200


def test_correction_via_web_updates_later_index(client):
    _upload(client, "2026-01", JAN)
    _upload(client, "2026-02", FEB, base_version=1)
    # 更正 2 月：B 由 220 -> 260
    corrected = "spec_code,unit_price\nA,110\nB,260\nD,80\n"
    r = _upload(client, "2026-02", corrected, base_version=2)
    assert r.status_code == 200
    conn = storage.connect(client.application.config["DB_PATH"])
    stats = {s["month"]: s for s in storage.load_stats(conn, 3)}
    from decimal import Decimal
    # 新环比 = (110+260)/(100+200) = 370/300
    assert stats["2026-02"]["ratio"] == Decimal("370") / Decimal("300")
    assert stats["2026-02"]["index"] == Decimal("100") * Decimal("370") / Decimal("300")
    conn.close()
