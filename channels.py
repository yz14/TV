"""
channels.py
-----------
从多个公开 IPTV 源聚合频道，按"规范化频道名"归并，每个频道保留多个备用 URL。
前端播放失败时可顺次尝试下一 URL，无需用户手动验证。

数据流：
    SOURCES (多个 M3U URL) → 并发抓取 → 解析 → 名称规范化 → 按规范名分桶
    → Channel{name, group, logo, urls:[...]} → 缓存（6h）
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

# 多个公开源；任意源失败不影响其它源。顺序决定 URL 优先级（先列出的源更可信）。
# 选源标准：(1) 覆盖传统央视/卫视；(2) 截至打包时间可达；(3) 体量适中
SOURCES = [
    # 覆盖最广、主流台备份多
    "https://raw.githubusercontent.com/vbskycn/iptv/master/tv/iptv4.m3u",
    # 台标最完整、IPv4 主流台齐全（27k+ ⭐ 项目，活跃维护）
    "https://raw.githubusercontent.com/fanmingming/live/main/tv/m3u/ipv4.m3u",
    # 自动每日构建 + 测速排序，新鲜度最好
    "https://raw.githubusercontent.com/Guovin/iptv-api/gd/output/ipv4/result.m3u",
    "https://raw.githubusercontent.com/Kimentanm/aptv/master/m3u/iptv.m3u",
    "https://raw.githubusercontent.com/qwerttvv/Beijing-IPTV/master/IPTV-Unicom.m3u",
    "https://iptv-org.github.io/iptv/countries/cn.m3u",
    "https://raw.githubusercontent.com/YanG-1989/m3u/main/Gather.m3u",
    # ----- 港澳台专用源（iptv-org 按地区切分；大多数流可能需要 HK/TW IP 才能播放）-----
    "https://iptv-org.github.io/iptv/countries/hk.m3u",
    "https://iptv-org.github.io/iptv/countries/tw.m3u",
    "https://iptv-org.github.io/iptv/countries/mo.m3u",
    # hujingguang/ChinaIPTV：手工维护，含 r.jdshipin.com 等可用转发短链（TVB News 等）
    "https://raw.githubusercontent.com/hujingguang/ChinaIPTV/main/HongKong.m3u8",
    "https://raw.githubusercontent.com/hujingguang/ChinaIPTV/main/cnTV_AutoUpdate.m3u8",
]

CACHE_FILE = Path(__file__).parent / "channels_cache.json"
CACHE_TTL_SECONDS = 6 * 3600

# 分组归一化：注意顺序，先匹配先生效
# 注意：港澳台 必须排在 卫视 前面 —— 否则 "凤凰卫视/中天卫视/民视卫视" 等会被先归到 卫视
GROUP_RULES: List[Tuple[str, List[str]]] = [
    ("央视",   ["央视", "cctv", "CCTV", "中央"]),
    ("港澳台", [
        # 香港 (HK) —— 中文名 + 高识别度英文台标
        # 注意：`翡翠台/明珠台` 必须用完整词，避免误匹配 "翡翠湾/明珠湖" 等大陆地名
        "香港", "凤凰", "鳳凰", "Phoenix", "TVB", "翡翠台", "明珠台", "无线", "無綫",
        "ViuTV", "Viu TV", "RTHK", "港台",
        # 台湾 (TW)
        "台湾", "臺灣", "中天", "民视", "民視", "华视", "華視", "公视", "公視", "台视", "台視",
        "三立", "东森", "東森", "八大", "纬来", "緯來", "TVBS", "年代", "壹电视", "壹電視",
        "镜电视", "鏡電視", "中视", "中視", "大爱", "大愛",
        # 澳门 (MO)
        "澳门", "澳門", "澳视", "澳視", "TDM",
    ]),
    ("卫视",   ["卫视", "衛視", "Satellite"]),
    ("少儿",   ["少儿", "卡通", "动画", "Kids", "儿童", "动漫"]),
    ("电影",   ["电影", "影视", "Movie", "Film", "影院"]),
    ("体育",   ["体育", "Sports", "足球", "篮球", "高尔夫"]),
    ("音乐",   ["音乐", "Music", "MTV", "MV"]),
    ("纪实",   ["纪录", "纪实", "Doc", "Documentary"]),
    ("新闻",   ["新闻", "News", "资讯"]),
    ("游戏",   ["游戏", "Game", "电竞", "赛事"]),
    ("国际",   ["国际", "海外", "International"]),
    # 把英文常见 group-title 也归类
    ("生活",   ["Lifestyle", "Family", "生活"]),
    ("综艺",   ["Entertainment", "综艺", "娱乐", "Comedy"]),
    ("地方",   ["地方", "电视台", "市", "省", "General"]),
]
DEFAULT_GROUP = "其他"

# 显式排除：垃圾 / 公告 / 推广频道
EXCLUDE_KEYWORDS = [
    "请勿贩卖", "免费订阅", "测试源", "更新时间", "公告",
    "温馨提示", "提示", "频道列表", "🚫",
]

# URL 路径中包含这些片段的视为纯音频流，过滤掉（如咪咕的 cctvN_audio 广播音频）
AUDIO_ONLY_URL_HINTS = [
    "/audio/", "_audio/", "/audio_", "audioonly", "audio_only",
    "/radio/", "/fm/",
]

# 已知失效或浏览器无法直接播放的 URL 模式；命中即剔除（不只是降级）
# - rtmp/rtsp/mms: hls.js 不支持，浏览器无法直接播放
# - 101.35.240.114:88/live.php: 多个公开源仍在引用，但该转发服务器自 2024 起整体 404
#   （vbskycn / suxuang / 等多个港澳台条目都指向它，导致"翡翠台/明珠台"等死链）
DEAD_URL_HINTS = [
    "rtmp://",
    "rtsp://",
    "mms://",
    "101.35.240.114:88/live.php",
]


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class Channel:
    name: str                       # 规范化显示名
    group: str
    logo: str = ""
    urls: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)  # 各源的原名（debug 用）

    def to_dict(self) -> Dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# 名称规范化
# ---------------------------------------------------------------------------

_CCTV_RE     = re.compile(r"cctv[\s\-_]*([0-9]{1,2}|5\+|plus|高清|hd)", re.IGNORECASE)
_QUALITY_RE  = re.compile(r"[\[\(【]?(超高清|高清|HD|4K|FHD|蓝光|BD|SD|标清|超清|1080P|720P|576P)[\]\)】]?",
                          re.IGNORECASE)
_GEOBLK_RE   = re.compile(r"\[?geo[-_]?blocked\]?|\[?not\s*24/7\]?", re.IGNORECASE)
_BRACKET_RE  = re.compile(r"[（(].*?[)）]")
_BRACKET2_RE = re.compile(r"[「【\[].*?[」】\]]")
_MULTI_SPACE = re.compile(r"\s+")

# 常见卫视前缀的别名映射，保证不同源的同台合并
_ALIAS_MAP = {
    "凤凰中文": "凤凰卫视中文台",
    "凤凰资讯": "凤凰卫视资讯台",
}


def normalize_name(raw: str) -> str:
    """把各源里的同台不同写法归一为统一显示名。"""
    s = raw.strip()
    # 去掉清晰度 / geo-blocked / 中括号注释
    s = _QUALITY_RE.sub("", s)
    s = _GEOBLK_RE.sub("", s)
    s = _BRACKET_RE.sub("", s)
    s = _BRACKET2_RE.sub("", s)
    # CCTV 系列统一为 "CCTV-N"
    m = _CCTV_RE.search(s)
    if m:
        token = m.group(1).lower()
        if token in ("plus",):
            token = "5+"
        s = _CCTV_RE.sub(f"CCTV-{token.upper() if token in ('5+','hd') else token}", s, count=1)
        # 修整：CCTV-5+
        s = s.replace("CCTV-5+", "CCTV-5+")
    # 多空格 / 多余符号
    s = s.replace("_", " ").replace("·", " ")
    s = _MULTI_SPACE.sub(" ", s).strip(" -·•|")
    # 别名替换
    s = _ALIAS_MAP.get(s, s)
    return s or raw.strip()


def normalize_key(name: str) -> str:
    """归并键：忽略大小写、空格、连字符。"""
    return re.sub(r"[\s\-_]+", "", name).lower()


# ---------------------------------------------------------------------------
# 自然排序键（用于 UI 内的频道展示顺序）
# ---------------------------------------------------------------------------
# 目标：让 CCTV-1, CCTV-2, ..., CCTV-5+, CCTV-6, ..., CCTV-10, ..., CCTV-13
# 按"人眼直觉"顺序出现，而不是字典序的 CCTV-1, CCTV-10, CCTV-11, ..., CCTV-2。
_NAT_SPLIT_RE = re.compile(r"(\d+)")


def natural_sort_key(name: str) -> List[Tuple[int, object]]:
    """把字符串拆成 (非数字段, 数字段) 交替的可比较元组列表。

    - 数字段 → (0, int(value))，整数比较 → "2" < "10"
    - 文字段 → (1, casefold)，统一大小写
    例:
        "CCTV-1"  → [(1,'cctv-'), (0,1)]
        "CCTV-10" → [(1,'cctv-'), (0,10)]
        "CCTV-5+" → [(1,'cctv-'), (0,5), (1,'+')]
    """
    s = (name or "").casefold()
    parts = _NAT_SPLIT_RE.split(s)
    key: List[Tuple[int, object]] = []
    for i, p in enumerate(parts):
        if i % 2 == 1:               # 奇数下标是 split 捕获组里的数字段
            key.append((0, int(p)))
        elif p:                      # 非空文字段
            key.append((1, p))
    return key or [(1, "")]


def _classify_group(raw_group: str, name: str) -> str:
    hay = f"{raw_group} {name}"
    for label, keys in GROUP_RULES:
        if any(k.lower() in hay.lower() for k in keys):
            return label
    # 严格归类：未命中规则一律归到「其他」，避免出现各种五花八门的杂项分组
    return DEFAULT_GROUP


# ---------------------------------------------------------------------------
# M3U 解析
# ---------------------------------------------------------------------------

_EXTINF_RE = re.compile(r'#EXTINF:-?\d+\s*(.*?),(.+)', re.IGNORECASE)
_ATTR_RE   = re.compile(r'([\w-]+)="([^"]*)"')


def parse_m3u(text: str) -> List[Dict]:
    """返回 [{name, group, logo, url}, ...]"""
    out: List[Dict] = []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.upper().startswith("#EXTINF"):
            m = _EXTINF_RE.match(line)
            if not m:
                i += 1
                continue
            attrs_str, raw_name = m.group(1), m.group(2).strip()
            attrs = dict(_ATTR_RE.findall(attrs_str))
            # 跳过 EXTVLCOPT / KODIPROP 等
            j = i + 1
            while j < len(lines) and lines[j].startswith("#"):
                j += 1
            if j < len(lines) and lines[j].startswith("http"):
                out.append({
                    "raw_name": raw_name,
                    "group":    attrs.get("group-title", ""),
                    "logo":     attrs.get("tvg-logo", ""),
                    "url":      lines[j],
                })
                i = j + 1
                continue
        i += 1
    return out


# ---------------------------------------------------------------------------
# 抓取 + 聚合
# ---------------------------------------------------------------------------

_HEADERS = {"User-Agent": "Mozilla/5.0 (OldTV/1.0)"}


def _fetch_one(url: str, timeout: int = 10) -> Optional[str]:
    try:
        logger.info("Fetching %s", url)
        r = requests.get(url, headers=_HEADERS, timeout=timeout)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
        return r.text
    except Exception as e:  # noqa: BLE001
        logger.warning("Source failed [%s]: %s", url, e)
        return None


def _fetch_all_parallel(sources: List[str]) -> List[Tuple[str, List[Dict]]]:
    """并发抓取所有源，保持原始顺序返回 [(source_url, parsed_entries), ...]。"""
    results: Dict[str, List[Dict]] = {s: [] for s in sources}
    with ThreadPoolExecutor(max_workers=len(sources)) as ex:
        futs = {ex.submit(_fetch_one, s): s for s in sources}
        for fut in as_completed(futs):
            src = futs[fut]
            text = fut.result()
            if text:
                try:
                    results[src] = parse_m3u(text)
                    logger.info("Parsed %d entries from %s", len(results[src]), src)
                except Exception as e:  # noqa: BLE001
                    logger.warning("Parse failed [%s]: %s", src, e)
    return [(s, results[s]) for s in sources]


def _is_excluded(name: str) -> bool:
    return any(k in name for k in EXCLUDE_KEYWORDS)


def _is_audio_only_url(url: str) -> bool:
    low = url.lower()
    return any(h in low for h in AUDIO_ONLY_URL_HINTS)


def _is_dead_url(url: str) -> bool:
    """命中已知死链/浏览器不支持协议的模式 → 直接丢弃。"""
    low = url.lower()
    return any(h in low for h in DEAD_URL_HINTS)


def _url_priority(url: str) -> Tuple[int, int]:
    """
    返回排序键：(主优先级, 次优先级)。值越小越优先。
    主优先级:
        0  - 直链 .m3u8 / .m3u（最可靠）
        1  - 其他
        2  - 含 ?id=、live.php、proxy 等查询/动态转发型（往往不稳定）
    次优先级:
        0  - https
        1  - http
    """
    low = url.lower()
    if "live.php" in low or "?id=" in low or "proxy.php" in low:
        primary = 2
    elif ".m3u8" in low or low.endswith(".m3u"):
        primary = 0
    else:
        primary = 1
    secondary = 0 if low.startswith("https://") else 1
    return (primary, secondary)


def _aggregate(per_source: List[Tuple[str, List[Dict]]]) -> List[Channel]:
    """按规范化名归并各源条目。"""
    merged: Dict[str, Channel] = {}
    audio_skipped = 0
    dead_skipped = 0
    for src, entries in per_source:
        for ent in entries:
            raw_name = ent["raw_name"]
            if _is_excluded(raw_name):
                continue
            # 过滤纯音频流（如 cmvideo 的 cctvN_audio 广播音频，会让用户看到黑屏只有声音）
            if _is_audio_only_url(ent["url"]):
                audio_skipped += 1
                continue
            # 剔除已知死链与不支持协议（RTMP/RTSP/MMS、已下线的转发服务器等）
            if _is_dead_url(ent["url"]):
                dead_skipped += 1
                continue
            name = normalize_name(raw_name)
            if not name:
                continue
            key = normalize_key(name)
            ch = merged.get(key)
            if ch is None:
                ch = Channel(
                    name=name,
                    group=_classify_group(ent["group"], name),
                    logo=ent["logo"] or "",
                    urls=[],
                    aliases=[],
                )
                merged[key] = ch
            else:
                # logo 缺失时补全
                if not ch.logo and ent["logo"]:
                    ch.logo = ent["logo"]
            if ent["url"] not in ch.urls:
                ch.urls.append(ent["url"])
            if raw_name not in ch.aliases:
                ch.aliases.append(raw_name)
    if audio_skipped:
        logger.info("Skipped %d audio-only URLs.", audio_skipped)
    if dead_skipped:
        logger.info("Skipped %d dead/unsupported URLs.", dead_skipped)
    # 对每个频道的 urls 按可靠性排序：直链 .m3u8/.m3u 在前，php?id=... 等查询型在后
    for ch in merged.values():
        ch.urls.sort(key=_url_priority)
    # 过滤无 URL 的
    return [c for c in merged.values() if c.urls]


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------

def _load_cache() -> Optional[List[Channel]]:
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if time.time() - data["ts"] > CACHE_TTL_SECONDS:
            logger.info("Cache expired.")
            return None
        return [Channel(**c) for c in data["channels"]]
    except Exception as e:  # noqa: BLE001
        logger.warning("Cache load failed: %s", e)
        return None


def _save_cache(channels: List[Channel]) -> None:
    try:
        CACHE_FILE.write_text(
            json.dumps(
                {"ts": time.time(), "channels": [c.to_dict() for c in channels]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        logger.info("Cache saved (%d channels).", len(channels))
    except Exception as e:  # noqa: BLE001
        logger.warning("Cache save failed: %s", e)


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def load_channels(force_refresh: bool = False) -> List[Channel]:
    """主入口：返回聚合后的频道列表。"""
    if not force_refresh:
        cached = _load_cache()
        if cached:
            logger.info("Loaded %d channels from cache.", len(cached))
            return cached

    per_source = _fetch_all_parallel(SOURCES)
    channels = _aggregate(per_source)
    if channels:
        # 按 URL 数量降序（多源备份更可靠的排前面）
        channels.sort(key=lambda c: (-len(c.urls), c.name))
        _save_cache(channels)
    else:
        logger.error("No channels obtained from any source!")
    return channels


def group_channels(channels: List[Channel]) -> Dict[str, List[Dict]]:
    """按分类聚合，保留预定义分组顺序；分组内按频道名自然排序。

    分组内排序使用 `natural_sort_key`，保证：
        CCTV-1, CCTV-2, ..., CCTV-5, CCTV-5+, CCTV-6, ..., CCTV-13
    而不是字典序下的 CCTV-1, CCTV-10, CCTV-11, ..., CCTV-2。
    """
    order = [g for g, _ in GROUP_RULES] + [DEFAULT_GROUP]
    buckets: Dict[str, List[Dict]] = {g: [] for g in order}
    for ch in channels:
        buckets.setdefault(ch.group, []).append(ch.to_dict())

    # 每个分组内部按频道名自然排序（稳定、可重现）
    for lst in buckets.values():
        lst.sort(key=lambda d: natural_sort_key(d.get("name", "")))

    result: Dict[str, List[Dict]] = {}
    for g in order:
        if buckets.get(g):
            result[g] = buckets[g]
    for g, lst in buckets.items():
        if g not in result and lst:
            result[g] = lst
    return result


def find_channel(channels: List[Channel], name: str) -> Optional[Channel]:
    """按规范化键查找。"""
    key = normalize_key(name)
    for c in channels:
        if normalize_key(c.name) == key:
            return c
    return None


# ---------------------------------------------------------------------------
# CLI 调试
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    chs = load_channels(force_refresh=True)
    print(f"\nTotal: {len(chs)} channels")
    multi = [c for c in chs if len(c.urls) > 1]
    print(f"With >1 backup URLs: {len(multi)}")
    print("\nTop 10 by backup count:")
    for c in chs[:10]:
        print(f"  [{c.group:>6}] {c.name:<25} urls={len(c.urls)}")
    groups = group_channels(chs)
    print("\nGroup distribution:")
    for g, lst in groups.items():
        print(f"  {g:>8}: {len(lst)}")
