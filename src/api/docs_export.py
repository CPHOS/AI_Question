"""
API 文档导出工具。

从 FastAPI 应用提取 OpenAPI 规范，生成到 ``docs/api/`` 目录下：

- ``openapi.json`` —— 机器可读的 OpenAPI 3.x 规范；
- ``index.html`` —— 基于 ReDoc 的离线可浏览文档（内联规范，可直接用 file:// 打开）。

用法::

    uv run physics-api-docs            # 写入默认 docs/api
    uv run physics-api-docs --out DIR  # 写入指定目录

应用自身在运行时也会通过 ``/docs``、``/redoc``、``/openapi.json`` 暴露同一份
规范；本工具用于把它固化成仓库内的静态文档。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from api.app import app
from config.config import PROJECT_ROOT, logger

# 规范以内联方式注入（而非 spec-url 外部引用）：
# 这样直接用 file:// 双击打开 index.html 也能正常渲染——避免 ReDoc 在浏览器
# 中解析外部文档时触发 "process is not defined" 的报错，且无需本地 HTTP 服务。
_REDOC_HTML = """<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>__TITLE__ · API 文档</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>body { margin: 0; padding: 0; }</style>
  </head>
  <body>
    <div id="redoc-container"></div>
    <script src="https://cdn.redoc.ly/redoc/latest/bundles/redoc.standalone.js"></script>
    <script id="openapi-spec" type="application/json">__SPEC__</script>
    <script>
      var spec = JSON.parse(document.getElementById("openapi-spec").textContent);
      Redoc.init(spec, {}, document.getElementById("redoc-container"));
    </script>
  </body>
</html>
"""


def _render_html(title: str, spec: dict) -> str:
    """把标题与内联 OpenAPI 规范渲染进 ReDoc HTML 模板。"""
    # 转义 ``</`` 以免 JSON 内容意外闭合 <script> 标签。
    spec_json = json.dumps(spec, ensure_ascii=False).replace("</", "<\\/")
    return _REDOC_HTML.replace("__TITLE__", title).replace("__SPEC__", spec_json)


def export(out_dir: Path) -> dict[str, Path]:
    """把 OpenAPI 规范与 ReDoc 页面写入 ``out_dir``。

    Args:
        out_dir: 输出目录（不存在时自动创建）。

    Returns:
        产物名称到路径的映射。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = app.openapi()

    openapi_path = out_dir / "openapi.json"
    openapi_path.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    index_path = out_dir / "index.html"
    index_path.write_text(_render_html(spec["info"]["title"], spec), encoding="utf-8")

    logger.info("[docs] 已导出 OpenAPI 规范: %s", openapi_path)
    logger.info("[docs] 已导出 ReDoc 页面: %s", index_path)
    return {"openapi": openapi_path, "index": index_path}


def main() -> None:
    """``physics-api-docs`` 脚本入口。"""
    parser = argparse.ArgumentParser(
        prog="physics-api-docs",
        description="导出 API OpenAPI 规范与 ReDoc 文档到 docs/api",
    )
    parser.add_argument(
        "--out", type=str, default=str(PROJECT_ROOT / "docs" / "api"),
        help="输出目录（默认: docs/api）",
    )
    args = parser.parse_args()
    paths = export(Path(args.out))
    for name, path in paths.items():
        print(f"[{name}] {path}")


if __name__ == "__main__":
    main()
