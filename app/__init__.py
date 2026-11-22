"""Flask 应用工厂与路由。"""
from __future__ import annotations

import os
from decimal import Decimal

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, url_for)

from . import storage
from .csvparse import parse_quotes_csv
from .errors import AppError, StaleVersionError
from .months import month_index, validate_month


def _decimal_default(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"不可序列化类型 {type(obj)}")


def create_app(db_path: str | None = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    app.config["DB_PATH"] = db_path or os.environ.get(
        "DB_PATH", os.path.join(os.getcwd(), "data", "prices.db")
    )
    os.makedirs(os.path.dirname(app.config["DB_PATH"]), exist_ok=True)

    def get_conn():
        if "conn" not in g:
            g.conn = storage.connect(app.config["DB_PATH"])
            storage.init_db(g.conn)
        return g.conn

    @app.teardown_appcontext
    def _close(_exc):
        conn = g.pop("conn", None)
        if conn is not None:
            conn.close()

    @app.errorhandler(AppError)
    def _handle_app_error(err: AppError):
        wants_json = request.path.startswith("/api/") or \
            "application/json" in request.headers.get("Accept", "")
        if wants_json:
            payload = {"error": type(err).__name__, "message": err.message}
            if isinstance(err, StaleVersionError):
                payload["retry"] = "请以最新版号为 base_version 重新拉取数据后提交"
            return jsonify(payload), err.status_code
        flash(err.message, "error")
        return redirect(url_for("index"))

    # ---------- 页面 ----------

    @app.get("/")
    def index():
        conn = get_conn()
        latest = storage.latest_version_id(conn)
        versions = storage.list_versions(conn)
        stats = storage.load_stats(conn, latest) if latest else []
        months = sorted((s["month"] for s in stats), key=month_index)
        return render_template(
            "index.html",
            versions=versions, latest=latest, stats=stats,
            months=months, base_version=latest,
        )

    @app.get("/versions/<int:version_id>")
    def version_detail(version_id: int):
        conn = get_conn()
        versions = storage.list_versions(conn)
        if not any(v["version_id"] == version_id for v in versions):
            abort(404, description=f"版号 v{version_id} 不存在")
        stats = storage.load_stats(conn, version_id)
        quotes = storage.load_quotes(conn, version_id)
        quote_counts = {m: len(rows) for m, rows in quotes.items()}
        return render_template(
            "version.html",
            version_id=version_id, versions=versions,
            latest=storage.latest_version_id(conn),
            stats=stats, quote_counts=quote_counts,
        )

    @app.get("/versions/<int:version_id>/months/<month>/quotes")
    def month_quotes(version_id: int, month: str):
        conn = get_conn()
        try:
            validate_month(month)
        except ValueError as exc:
            abort(404, description=str(exc))
        versions = storage.list_versions(conn)
        if not any(v["version_id"] == version_id for v in versions):
            abort(404, description=f"版号 v{version_id} 不存在")
        quotes = storage.load_quotes(conn, version_id)
        if month not in quotes:
            abort(404, description=f"v{version_id} 中没有 {month} 的报价")
        stats = {s["month"]: s for s in storage.load_stats(conn, version_id)}
        return render_template(
            "quotes.html",
            version_id=version_id, month=month,
            rows=sorted(quotes[month], key=lambda r: r["spec_code"]),
            stat=stats.get(month), versions=versions,
            latest=storage.latest_version_id(conn),
        )

    @app.post("/publish")
    def publish_form():
        get_conn()  # 确保错误处理器能复用连接
        _do_publish(request)
        flash("发布成功，已生成新版本。", "ok")
        return redirect(url_for("index"))

    # ---------- JSON API ----------

    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    @app.get("/api/versions")
    def api_versions():
        conn = get_conn()
        versions = storage.list_versions(conn)
        latest = storage.latest_version_id(conn)
        return jsonify({"latest_version": latest, "versions": versions})

    @app.get("/api/versions/<int:version_id>")
    def api_version(version_id: int):
        conn = get_conn()
        versions = storage.list_versions(conn)
        if not any(v["version_id"] == version_id for v in versions):
            return jsonify({"error": "NotFound",
                            "message": f"版号 v{version_id} 不存在"}), 404
        stats = storage.load_stats(conn, version_id)
        quotes = storage.load_quotes(conn, version_id)
        return jsonify({
            "version_id": version_id,
            "months": stats,
            "quote_counts": {m: len(r) for m, r in quotes.items()},
        })

    @app.post("/api/publish")
    def api_publish():
        result = _do_publish(request)
        return jsonify({
            "version_id": result["version_id"],
            "kind": result["kind"],
            "target_month": result["target_month"],
            "created_at": result["created_at"],
            "months": result["stats"],
        }), 201

    def _do_publish(req):
        month = (req.form.get("month") or "").strip()
        try:
            validate_month(month)
        except ValueError as exc:
            raise AppError(str(exc)) from exc
        upload = req.files.get("csv_file")
        if upload is None or not upload.filename:
            raise AppError("请选择报价 CSV 文件")
        raw = upload.read()
        if not raw.strip():
            raise AppError("上传的 CSV 为空")
        rows = parse_quotes_csv(raw)
        base_raw = req.form.get("base_version")
        base_version = int(base_raw) if base_raw else None
        return storage.publish(get_conn(), month, rows, base_version)

    # Decimal 在 jsonify 下的兜底
    app.json.default = _decimal_default

    @app.template_filter("money")
    def _money(value):
        return "—" if value is None else f"{value.quantize(Decimal('0.01'))}"

    @app.template_filter("index4")
    def _index4(value):
        return "—" if value is None else f"{value.quantize(Decimal('0.01'))}"

    @app.template_filter("ratio4")
    def _ratio4(value):
        return "—" if value is None else f"{value.quantize(Decimal('0.0001'))}"

    return app
