"""
OldTV - 本地直播 TV 工具
========================
启动:
    python app.py [--host 127.0.0.1] [--port 5000] [--debug]

访问:
    http://127.0.0.1:5000
"""

from __future__ import annotations

import argparse
import logging
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Flask, jsonify, render_template, request

import channels as channels_mod
from proxy import bp as proxy_bp, make_play_url


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("oldtv")


# ---------------------------------------------------------------------------
# Flask 应用
# ---------------------------------------------------------------------------
def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    # 保留 group_channels() 返回的字典顺序，否则 jsonify 会按字母排序
    app.json.sort_keys = False
    app.register_blueprint(proxy_bp)

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/channels")
    def api_channels():
        force = request.args.get("refresh") == "1"
        chs = channels_mod.load_channels(force_refresh=force)
        grouped = channels_mod.group_channels(chs)
        # 把每个频道的 urls 转成代理后的播放 URL 数组（按优先级）
        for _group, lst in grouped.items():
            for ch in lst:
                ch["plays"] = [make_play_url(u)[0] for u in ch.get("urls", [])]
                # 删掉原始 urls，避免暴露上游
                ch.pop("urls", None)
                ch.pop("aliases", None)
        return jsonify({
            "total": sum(len(v) for v in grouped.values()),
            "groups": grouped,
        })

    @app.route("/api/test_channel")
    def api_test_channel():
        """
        手动验证端点：并发探测某频道所有 URL 是否可达 & 是 m3u8。
        Query: ?name=频道名
        返回每个 URL 的状态，便于用户判断哪条源能用。
        """
        name = request.args.get("name", "").strip()
        if not name:
            return jsonify({"error": "missing 'name'"}), 400
        chs = channels_mod.load_channels()
        ch = channels_mod.find_channel(chs, name)
        if not ch:
            return jsonify({"error": f"channel not found: {name}"}), 404

        def _probe(url: str):
            try:
                r = requests.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=4,
                    stream=True,
                    allow_redirects=True,
                )
                head_bytes = b""
                for chunk in r.iter_content(2048):
                    head_bytes += chunk
                    if len(head_bytes) >= 2048:
                        break
                r.close()
                is_m3u8 = b"#EXTM3U" in head_bytes
                return {
                    "url": url,
                    "status": r.status_code,
                    "ok": r.status_code == 200 and is_m3u8,
                    "is_m3u8": is_m3u8,
                }
            except Exception as e:
                return {"url": url, "status": f"ERR:{type(e).__name__}",
                        "ok": False, "error": str(e)}

        with ThreadPoolExecutor(max_workers=min(8, len(ch.urls) or 1)) as ex:
            results = list(ex.map(_probe, ch.urls))

        return jsonify({
            "name": ch.name,
            "group": ch.group,
            "total": len(results),
            "alive": sum(1 for r in results if r["ok"]),
            "results": results,
        })

    @app.errorhandler(404)
    def _404(_e):
        return jsonify({"error": "not found"}), 404

    return app


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="OldTV - local IPTV viewer")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    app = create_app()
    # 启动时预热缓存（不阻塞失败）
    try:
        n = len(channels_mod.load_channels())
        logger.info("Channel list ready: %d channels.", n)
    except Exception as e:  # noqa: BLE001
        logger.warning("Channel preload failed (will retry on request): %s", e)

    logger.info("Serving on http://%s:%d", args.host, args.port)
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
