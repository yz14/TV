"""直接 probe hujingguang/ChinaIPTV 的 r.jdshipin.com 短链 + 拉完整 HongKong m3u 看看可达性。"""
import requests

# 拉 hujingguang HongKong.m3u8 的所有 URL，逐个 probe
r = requests.get("https://raw.githubusercontent.com/hujingguang/ChinaIPTV/main/HongKong.m3u8",
                 headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
text = r.text
print("=== HongKong.m3u8 全文 ===")
print(text)
print("\n=== 探测每个 URL ===")
lines = text.splitlines()
for i, ln in enumerate(lines):
    if ln.startswith("http"):
        try:
            pr = requests.get(ln, headers={"User-Agent": "Mozilla/5.0"}, timeout=8, stream=True, allow_redirects=True)
            head = next(pr.iter_content(512), b"").decode("utf-8", errors="replace")[:120]
            print(f"  {pr.status_code}  {ln}")
            print(f"      final={pr.url}")
            print(f"      head={head!r}")
        except Exception as e:
            print(f"  ERR  {ln}  ({e})")
