"""探测指定频道每个上游 URL 的可达性与内容类型。"""
import json
import sys
import requests

HEADERS = {"User-Agent": "Mozilla/5.0"}

def probe(url, timeout=8):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, stream=True, allow_redirects=True)
        body_head = ""
        try:
            body_head = next(r.iter_content(chunk_size=512)).decode("utf-8", errors="replace")
        except Exception:
            pass
        return {
            "status": r.status_code,
            "ctype": r.headers.get("Content-Type", ""),
            "final": r.url,
            "head": body_head[:200],
        }
    except Exception as e:
        return {"error": str(e)}

def main():
    names = sys.argv[1:] or ["翡翠台", "台视"]
    d = json.load(open("channels_cache.json", encoding="utf-8"))
    for name in names:
        match = [c for c in d["channels"] if name.lower() in c["name"].lower()]
        for c in match:
            print(f"\n=== {c['name']}  ({c['group']}) ===")
            print(f"aliases: {c['aliases']}")
            for i, url in enumerate(c["urls"]):
                print(f"[{i}] {url}")
                info = probe(url)
                print(f"    -> {info}")

if __name__ == "__main__":
    main()
