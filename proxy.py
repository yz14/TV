"""
proxy.py
--------
HLS 流代理。浏览器端 hls.js 通过 XHR 拉取 .m3u8 + .ts，会触发 CORS / Mixed-Content
阻断；本模块由 Flask 后端代为请求上游，并对 manifest 内所有引用 URL 做改写，
让所有子请求继续走代理。

支持：
    - 主播放列表 / 媒体播放列表
    - 嵌套 m3u8（master -> variant）
    - #EXT-X-KEY URI="..."         (AES 加密 key)
    - #EXT-X-MAP URI="..."         (fMP4 init segment)
    - 二进制切片 (.ts / .m4s / .key / .aac 等) 流式转发
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Tuple
from urllib.parse import urljoin

import requests
from flask import Blueprint, Response, abort, request, stream_with_context

logger = logging.getLogger(__name__)

bp = Blueprint("proxy", __name__)

# 上游请求通用 headers
_UPSTREAM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (OldTV) AppleWebKit/537.36 (KHTML, like Gecko)",
    "Accept": "*/*",
}

# 匹配标签行中的 URI="..."
_URI_ATTR_RE = re.compile(r'(URI=")([^"]+)(")')


# ---------------------------------------------------------------------------
# URL 编解码
# ---------------------------------------------------------------------------

def encode_url(url: str) -> str:
    return base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")


def decode_url(token: str) -> str:
    pad = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + pad).decode("utf-8")


# ---------------------------------------------------------------------------
# Manifest 改写
# ---------------------------------------------------------------------------

def _is_m3u8(url: str) -> bool:
    u = url.split("?", 1)[0].lower()
    return u.endswith(".m3u8") or u.endswith(".m3u")


def _proxied(url: str, kind: str) -> str:
    """生成本服务下的代理 URL。kind in {'m3u8','seg'}。"""
    return f"/proxy/{kind}/{encode_url(url)}"


def _rewrite_manifest(text: str, base_url: str) -> str:
    """改写 m3u8 文本中所有引用 URL。

    上下文规则:
        - `#EXT-X-STREAM-INF` / `#EXT-X-I-FRAME-STREAM-INF` 之后的 URL 行是
          子 m3u8（variant playlist），即便没有 .m3u8 后缀也强制按 m3u8 处理。
        - `#EXT-X-MEDIA` 标签里 URI= 也是子 m3u8。
        - `#EXT-X-KEY` / `#EXT-X-MAP` 里 URI= 是二进制片段。
        - 其它（紧跟在 #EXTINF 之后）的 URL 行视为媒体 segment。
    """
    out_lines = []
    next_is_variant = False     # 下一行 URL 行强制为 m3u8

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            out_lines.append(line)
            continue

        if stripped.startswith("#"):
            tag = stripped.split(":", 1)[0].upper()

            # 处理标签内 URI="..."
            def _repl(m: re.Match, _tag=tag) -> str:
                inner = m.group(2)
                absolute = urljoin(base_url, inner)
                # EXT-X-MEDIA 的 URI 通常指向子 m3u8（音轨/字幕），但 EXT-X-KEY/EXT-X-MAP 是二进制
                if _tag == "#EXT-X-MEDIA":
                    kind = "m3u8"
                elif _tag in ("#EXT-X-KEY", "#EXT-X-MAP"):
                    kind = "seg"
                else:
                    kind = "m3u8" if _is_m3u8(absolute) else "seg"
                return f'{m.group(1)}{_proxied(absolute, kind)}{m.group(3)}'

            out_lines.append(_URI_ATTR_RE.sub(_repl, line))
            # 标记：若是 STREAM-INF / I-FRAME-STREAM-INF，则下一行 URL 一定是 variant m3u8
            if tag in ("#EXT-X-STREAM-INF", "#EXT-X-I-FRAME-STREAM-INF"):
                next_is_variant = True
            continue

        # URL 行
        absolute = urljoin(base_url, stripped)
        if next_is_variant:
            kind = "m3u8"
            next_is_variant = False
        else:
            kind = "m3u8" if _is_m3u8(absolute) else "seg"
        out_lines.append(_proxied(absolute, kind))

    return "\n".join(out_lines) + "\n"


# ---------------------------------------------------------------------------
# Flask 路由
# ---------------------------------------------------------------------------

def _decode_or_400(token: str) -> str:
    try:
        return decode_url(token)
    except Exception as e:  # noqa: BLE001
        logger.warning("Bad proxy token %r: %s", token, e)
        abort(400, "Bad URL token")


@bp.route("/proxy/m3u8/<token>")
def proxy_m3u8(token: str):
    upstream = _decode_or_400(token)
    logger.info("PROXY m3u8 -> %s", upstream)
    try:
        r = requests.get(upstream, headers=_UPSTREAM_HEADERS, timeout=15)
    except requests.RequestException as e:
        logger.error("Upstream m3u8 error: %s", e)
        return Response(f"Upstream error: {e}", status=502)

    if r.status_code != 200:
        return Response(r.content, status=r.status_code,
                        content_type=r.headers.get("Content-Type", "text/plain"))

    # 用上游真实 URL 作为 base（处理 301/302 后）
    base_url = r.url
    try:
        text = r.content.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        text = r.text
    rewritten = _rewrite_manifest(text, base_url)
    resp = Response(rewritten, content_type="application/vnd.apple.mpegurl")
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/proxy/seg/<token>")
def proxy_segment(token: str):
    upstream = _decode_or_400(token)
    # 透传 Range，支持断点/seek
    headers = dict(_UPSTREAM_HEADERS)
    if "Range" in request.headers:
        headers["Range"] = request.headers["Range"]

    try:
        r = requests.get(upstream, headers=headers, stream=True, timeout=20)
    except requests.RequestException as e:
        logger.error("Upstream seg error: %s", e)
        return Response(f"Upstream error: {e}", status=502)

    content_type = r.headers.get("Content-Type", "")
    status = r.status_code

    # === 内容嗅探兜底 ===
    # 有些 master playlist 把变体 m3u8 写成无后缀 URL（如 https://cdn3.x.xyz/.../cctv13），
    # 上层会把它当 seg 路由过来。这里嗅前几个字节，若实际是 m3u8 就 fallback 到 manifest 改写。
    body_iter = r.iter_content(chunk_size=64 * 1024)
    sniff = b""
    if status == 200 and "Range" not in headers:
        try:
            sniff = next(body_iter, b"")
        except Exception:  # noqa: BLE001
            sniff = b""

        looks_like_m3u8 = (
            sniff[:7] == b"#EXTM3U"
            or "mpegurl" in content_type.lower()
        )
        if looks_like_m3u8:
            # 把剩余 body 读完再统一重写
            rest = b""
            try:
                for chunk in body_iter:
                    rest += chunk
            except Exception:  # noqa: BLE001
                pass
            base_url = r.url
            r.close()
            text = (sniff + rest).decode("utf-8", errors="replace")
            logger.info("PROXY seg fallback to m3u8 for %s", upstream)
            rewritten = _rewrite_manifest(text, base_url)
            resp = Response(rewritten, content_type="application/vnd.apple.mpegurl")
            resp.headers["Access-Control-Allow-Origin"] = "*"
            resp.headers["Cache-Control"] = "no-store"
            return resp

    if not content_type:
        content_type = "application/octet-stream"

    def generate(_first=sniff, _rest=body_iter, _resp=r):
        try:
            if _first:
                yield _first
            for chunk in _rest:
                if chunk:
                    yield chunk
        finally:
            _resp.close()

    resp = Response(stream_with_context(generate()),
                    status=status, content_type=content_type)
    # 透传若干响应头
    for h in ("Content-Length", "Content-Range", "Accept-Ranges"):
        if h in r.headers:
            resp.headers[h] = r.headers[h]
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


# ---------------------------------------------------------------------------
# 工具：把原始 url 转成首个 m3u8 代理 url（前端 API 用）
# ---------------------------------------------------------------------------

def make_play_url(original_url: str) -> Tuple[str, str]:
    """返回 (proxied_url, kind)。"""
    kind = "m3u8" if _is_m3u8(original_url) else "seg"
    return _proxied(original_url, kind), kind
