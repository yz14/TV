"""验证候选港澳台社区源的可达性和翡翠/台视等覆盖情况。"""
import re
import requests

CANDIDATES = [
    "https://raw.githubusercontent.com/joevess/IPTV/main/sources/iptv.m3u",
    "https://raw.githubusercontent.com/joevess/IPTV/main/home.m3u",
    "https://raw.githubusercontent.com/joevess/IPTV/main/iptv.m3u",
    "https://raw.githubusercontent.com/YueChan/Live/main/Global.m3u",
    "https://raw.githubusercontent.com/YueChan/Live/main/IPTV.m3u",
    "https://raw.githubusercontent.com/qist/tvbox/master/m3u/tvbox.m3u",
    "https://raw.githubusercontent.com/BurningC4/Chinese-IPTV/master/TV-IPV4.m3u",
    "https://live.fanmingming.com/tv/m3u/global.m3u",
    "https://live.fanmingming.com/tv/m3u/itv.m3u",
    "https://raw.githubusercontent.com/Guovin/iptv-api/gd/output/ipv4/hongkong.m3u",
    "https://raw.githubusercontent.com/Guovin/iptv-api/gd/output/ipv4/taiwan.m3u",
    "https://raw.githubusercontent.com/Guovin/iptv-api/gd/output/ipv4/macao.m3u",
]

KEYS_HK = ["翡翠", "明珠", "TVB", "凤凰", "ViuTV", "RTHK", "now", "港台"]
KEYS_TW = ["台视", "台視", "中视", "中視", "华视", "華視", "公视", "公視", "三立", "TVBS", "民视", "民視"]

for url in CANDIDATES:
    print(f"\n--- {url}")
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        print(f"  HTTP {r.status_code}, size={len(r.content)}")
        if r.status_code != 200 or len(r.content) < 1000:
            continue
        text = r.text
        lines = text.splitlines()
        hk_hits = sum(1 for ln in lines if ln.startswith("#EXTINF") and any(k.lower() in ln.lower() for k in KEYS_HK))
        tw_hits = sum(1 for ln in lines if ln.startswith("#EXTINF") and any(k.lower() in ln.lower() for k in KEYS_TW))
        print(f"  HK-hits={hk_hits}  TW-hits={tw_hits}")
        # show 2 翡翠 examples
        for i, ln in enumerate(lines):
            if "#EXTINF" in ln and "翡翠" in ln:
                print(f"    [翡翠] {ln[:120]}  ==>  {lines[i+1][:120] if i+1<len(lines) else ''}")
                break
        for i, ln in enumerate(lines):
            if "#EXTINF" in ln and ("台视" in ln or "台視" in ln):
                print(f"    [台视] {ln[:120]}  ==>  {lines[i+1][:120] if i+1<len(lines) else ''}")
                break
    except Exception as e:
        print(f"  ERR: {e}")
