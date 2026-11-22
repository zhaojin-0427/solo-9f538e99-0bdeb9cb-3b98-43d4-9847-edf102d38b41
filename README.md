# 建材价格指数发布台（Flask + SQLite）

面向工程造价员的建材价格指数发布与跨月调价复核工具。每次发布选**一个月份**、
上传**一个报价 CSV**，系统按版本化方式保存全部报价与逐月指数，可按版号回溯
任意新旧发布版。

## 业务规则

1. **CSV 格式**：UTF-8（可带 BOM），表头含 `spec_code,unit_price` 两列
   （允许额外列、允许 `#` 注释行）；规格码文件内唯一，单价必须为正数。
2. **首月任选**：库为空时第一个月份可任意指定，首月指数 = 100。
3. **续报下月**：已有发布后，新月必须是当前最后月份的下一个自然月，否则拒绝。
4. **整月更正**：对已存在的月份再次提交即整月替换该月报价，生成新版本。
5. **环比与指数**：相邻两月**只比较共有规格**，
   环比 = 本月共有规格单价合计 ÷ 上月共有规格单价合计；
   指数 = 首月 100 起逐月乘以环比（Decimal 高精度计算，不做中间舍入）。
6. **更正作用域**：更正月 M 只重算 (M-1, M)、(M, M+1) 两个环比，
   计算中只读取 M-1、M、M+1 三个月的报价明细；其后各月环比沿用旧版，
   指数随新的连乘链全部更新。
7. **无共有规格**：相邻两月共有规格为空时，错误信息指出这两个月份并
   **拒绝换版**（HTTP 422），不产生新版本。
8. **并发控制**：写事务使用 `BEGIN IMMEDIATE` 串行化，实际发布顺序即拿锁顺序；
   提交携带 `base_version`，若库内最新版已推进则返回 **409 并提示重试**，
   以新版号重新提交即可。
9. **版本快照**：每次换版整体快照报价明细与逐月统计，核算期间可按版号查询
   任意旧版的各月指数、环比、参与环比的共有规格数与报价明细。

## 快速启动（docker compose）

```bash
docker compose up --build
# 浏览器打开 http://localhost:8000
```

SQLite 数据保存在命名卷 `price-index-data`（容器内 `/data/prices.db`）。
停止与清理：

```bash
docker compose down            # 停止，数据保留
docker compose down -v         # 连同数据卷一起删除
```

## 本地运行（无 Docker）

```bash
pip install -r requirements.txt
python -m gunicorn --bind 0.0.0.0:8000 wsgi:app     # 或 python wsgi.py
# 默认库文件 ./data/prices.db，可用环境变量 DB_PATH 覆盖
```

## 页面与接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 发布表单、版本列表、最新版逐月指数 |
| POST | `/publish` | 表单发布（字段 `month`、`csv_file`、`base_version`） |
| GET | `/versions/<id>` | 任一版号的逐月指数与共有规格数 |
| GET | `/versions/<id>/months/<YYYY-MM>/quotes` | 该版该月报价明细，标注是否参与环比 |
| GET | `/api/versions` | 版本列表 JSON |
| GET | `/api/versions/<id>` | 单版各月统计 JSON |
| POST | `/api/publish` | JSON API 发布，`multipart/form-data`，成功 201，冲突 409 |
| GET | `/healthz` | 健康检查 |

## 自测

```bash
pip install pytest
python -m pytest tests/ -q
```

## 目录结构

```
app/
  __init__.py      Flask 工厂、路由、错误处理
  months.py        月份校验与自然月加减
  csvparse.py      CSV 解析与行级校验
  engine.py        环比/指数纯函数引擎 + 更正作用域重算
  storage.py       SQLite 版本快照、写事务串行化、乐观版号
  templates/       页面模板
  static/          样式
samples/           三个月示例 CSV 与“无共有规格”示例
tests/             引擎、CSV、存储、并发、HTTP 端到端测试
Dockerfile, docker-compose.yml, wsgi.py, requirements.txt
```
