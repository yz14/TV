"""直接从 iptv-org 拉 hk/tw m3u，看翡翠/台视的官方 URL。"""
import re
import requests
from channels import parse_m3u, _is_audio_only_url, _is_excluded, AUDIO_ONLY_URL_HINTS

URLS = [
    "https://iptv-org.github.io/iptv/countries/hk.m3u",
    "https://iptv-org.github.io/iptv/countries/tw.m3u",
]
KEYS = ["jade", "翡翠", "tvb", "ttv", "台视", "台視", "litv", "cts", "ctv"]

for src in URLS:
    print(f"\n===== {src} =====")
    r = requests.get(src, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    r.encoding = r.apparent_encoding or "utf-8"
    entries = parse_m3u(r.text)
    print(f"total entries: {len(entries)}")
    for ent in entries:
        name = ent["raw_name"]
        url = ent["url"]
        hit = any(k.lower() in name.lower() or k.lower() in url.lower() for k in KEYS)
        if hit:
            audio = _is_audio_only_url(url)
            excl = _is_excluded(name)
            print(f"  name={name!r}")
            print(f"    url={url}")
            print(f"    audio_filter={audio}  excluded={excl}")
