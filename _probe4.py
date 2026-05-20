"""验证 suxuang/myIPTV 和 hujingguang/ChinaIPTV 等候选源。"""
import requests

CANDIDATES = [
    "https://raw.githubusercontent.com/suxuang/myIPTV/main/ipv4.m3u",
    "https://raw.githubusercontent.com/hujingguang/ChinaIPTV/main/HongKong.m3u8",
    "https://raw.githubusercontent.com/hujingguang/ChinaIPTV/main/Taiwan.m3u8",
    "https://raw.githubusercontent.com/hujingguang/ChinaIPTV/main/cnTV_AutoUpdate.m3u8",
    "https://raw.githubusercontent.com/nthack/IPTVM3U/main/HK.m3u",
    "https://raw.githubusercontent.com/nthack/IPTVM3U/master/HK.m3u",
]

KEYS_HK = ["翡翠", "明珠", "TVB", "ViuTV", "now", "凤凰", "RTHK"]
KEYS_TW = ["台视", "台視", "中视", "中視", "华视", "華視", "TVBS", "民视", "三立"]

for url in CANDIDATES:
    print(f"\n--- {url}")
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        print(f"  HTTP {r.status_code}, size={len(r.content)}")
        if r.status_code != 200 or len(r.content) < 500:
            continue
        text = r.text
        lines = text.splitlines()
        # find TVB Jade / 翡翠 entry
        for i, ln in enumerate(lines):
            if "#EXTINF" in ln and ("翡翠" in ln or "Jade" in ln or "TVB" in ln):
                nxt = lines[i+1] if i+1 < len(lines) else ""
                # may skip non-http lines (KODIPROP/EXTVLCOPT)
                j = i + 1
                while j < len(lines) and lines[j].startswith("#"):
                    j += 1
                if j < len(lines):
                    nxt = lines[j]
                print(f"  [HK] {ln[:100]}")
                print(f"       -> {nxt[:120]}")
                break
        for i, ln in enumerate(lines):
            if "#EXTINF" in ln and ("台视" in ln or "台視" in ln):
                j = i + 1
                while j < len(lines) and lines[j].startswith("#"):
                    j += 1
                if j < len(lines):
                    print(f"  [TW] {ln[:100]}")
                    print(f"       -> {lines[j][:120]}")
                break
    except Exception as e:
        print(f"  ERR: {e}")
