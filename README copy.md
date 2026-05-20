# OldTV · 本地直播 TV 工具

一个开箱即用的本地直播 IPTV 工具：浏览器打开即可看 **CCTV / 各大卫视 / 地方台 / 港澳台** 等公开直播频道，点击频道按钮即全屏播放。

## 特性
- 📺 **一键观看**：分类网格 + 搜索，点击即全屏
- 🌐 **多源聚合**：默认聚合 7 个公开 M3U 源 (`vbskycn / fanmingming / Guovin / Kimentanm / qwerttvv / iptv-org / YanG-1989`)；同名频道收集**多个备用 URL**（主要卫视通常有 5-8 个备份源）
- 🔁 **自动 fallback**：当前源播放失败时**自动顺次切换**到下一个源，无需任何手动操作
- 🩺 **后端探活**：播放栏 `🩺 验证` 按钮调用 `/api/test_channel` 并发探测所有源，把可用源排到前面
- 🎛️ **手动切源**：播放栏 `◀ 源` / `源 ▶` 按钮（或 `Alt+←/→`）手动切到上/下一源
- 🛡️ **内置 HLS 代理**：后端代理 m3u8 + ts 切片，绕过浏览器 CORS / Mixed-Content
- 🧩 **模块化**：`channels.py` 抓取/解析/聚合 / `proxy.py` 流代理 / `app.py` Flask 路由 / 前端独立
- 🐛 **日志友好**：Python `logging` 全程跟踪上游请求与缓存命中

## 目录结构
```
OldTV/
├── app.py                # Flask 入口
├── channels.py           # M3U 抓取 + 解析 + 缓存
├── proxy.py              # HLS 流代理（m3u8 改写 + 切片转发）
├── requirements.txt
├── templates/index.html
└── static/
    ├── css/style.css
    └── js/app.js         # 使用 hls.js
```

## 快速开始

测试环境：`D:\miniconda\envs\torch27_env\python.exe`

```powershell
# 1. 安装依赖
D:\miniconda\envs\torch27_env\python.exe -m pip install -r requirements.txt

# 2. 启动
D:\miniconda\envs\torch27_env\python.exe app.py

# 3. 浏览器打开
#    http://127.0.0.1:5000
```

可选参数：
```
python app.py --host 0.0.0.0 --port 8080 --debug
```

## 使用说明
- 顶部输入框可跨分组搜索频道（例如 `CCTV1`、`湖南卫视`）
- 卡片右上角徽章显示该频道有几个备份源
- 点击「⟳ 刷新」按钮可强制重新拉取最新频道列表（清空缓存）
- 进入播放后会自动全屏；按 `Esc` 或点 `✕` 退出
- 当前源失败时**自动切下一源**；状态栏会显示 `源 i/N` 当前进度
- 手动切源：播放栏 `◀ 源` / `源 ▶` 或快捷键 `Alt+←` / `Alt+→`
- `🩺 验证`：后端并发探测当前频道所有源，把可用源排到前面（适合自动切换还是不顺利时用）

## 频道来源
默认聚合以下公开源（顺序即优先级）：

**大陆 / 综合源**
1. `vbskycn/iptv`（覆盖广，含 CCTV/卫视/地方）
2. `fanmingming/live` (ipv4.m3u，台标最完整、27k+ ⭐ 活跃维护)
3. `Guovin/iptv-api` (自动每日构建 + 测速排序，新鲜度最好)
4. `Kimentanm/aptv`
5. `qwerttvv/Beijing-IPTV`
6. `iptv-org/iptv` (cn.m3u)
7. `YanG-1989/m3u` (游戏/剧集类)

**港澳台专用源**（`iptv-org` 按地区切分，覆盖 TVB 翡翠/凤凰/RTHK/TVBS/台视/纬来/TDM 等主要广播台）
8. `iptv-org/iptv/countries/hk.m3u`（香港）
9. `iptv-org/iptv/countries/tw.m3u`（台湾）
10. `iptv-org/iptv/countries/mo.m3u`（澳门）

可在 `channels.py` 的 `SOURCES` 中增删（本地 M3U 文件需先上传到任意 HTTP 服务）。  
注意：所有源均为社区维护，链接可能时有变化或失效。

> ⚠️ **关于港澳台播放**：拉取频道列表本身**不需要 VPN**（M3U 文件托管在 GitHub Pages 上）；但实际播放时，TVB / TVBS / RTHK / TDM 的流通常做 **GeoIP 区域限制**，从大陆直连大概率黑屏 / 403。  
> - 若需正常播放，请开启 **香港 / 台湾 / 澳门节点的 VPN 或代理**
> - 凤凰卫视中文/资讯台（央视体系下放）通常无区域限制，可直接播放
> - 播放失败会自动 fallback；播放时可点 `🩺 验证` 后端并发探活，把可用源排到前面

> ⚠️ **关于 IPv6**：`fanmingming/live` 同时提供 `ipv6.m3u`，但绝大多数家庭宽带不能直连 IPv6 流（会黑屏），所以本项目固定用 `ipv4.m3u`；若你网络支持 IPv6 可自行替换。

## 工作原理
1. 启动时**并发**从多个公开 M3U 源拉取频道清单
2. 解析 `#EXTINF` 行的 `group-title / tvg-logo / tvg-name`
3. **频道名规范化**（去清晰度后缀、统一 CCTV-N 命名）→ **按规范化键归并** → 同台不同源合并为一个 `Channel{urls:[...]}`
4. 按关键字归类（央视/卫视/港澳台/少儿/电影/...），缓存到 `channels_cache.json`（默认 6 小时）
5. 前端 `GET /api/channels` 拿到 `plays: [proxied1, proxied2, ...]` 数组
6. hls.js 请求 `/proxy/m3u8/<token>`：
   - 后端拉取上游 m3u8，**改写其中所有引用 URL（嵌套 m3u8、ts 切片、EXT-X-KEY、EXT-X-MAP）** 指向 `/proxy/seg/<token>`
   - 切片由后端流式转发，透传 `Range` 等头
7. 当 hls.js 抛出 fatal 错误（manifest 加载超时 / 404 / 媒体错误），前端自动尝试 `plays[i+1]`
8. 整条 HLS 链路经本地，无跨域问题

## 常见问题
- **某些频道打不开 / 黑屏**：会自动 fallback 到下一源；如仍不行可点 `🩺 验证`。公开 IPTV 流本身就时常失效 / 区域限制
- **首次加载较慢**：需要并发拉取 + 解析多份 M3U；后续读缓存秒开
- **想全部走 HTTPS**：本工具默认 HTTP 仅供本机使用；如需对外暴露请自行套 Nginx + TLS

## API
| Method | Path | 说明 |
|---|---|---|
| GET | `/` | 前端页面 |
| GET | `/api/channels[?refresh=1]` | 返回分组频道及代理播放 URL 列表 |
| GET | `/api/test_channel?name=频道名` | 并发探测该频道所有上游 URL 是否可达 |
| GET | `/proxy/m3u8/<token>` | HLS manifest 代理（含子 URL 重写） |
| GET | `/proxy/seg/<token>` | HLS 切片 / 二进制流代理 |

## 法律说明
本工具不内置任何直播流，只是公开 M3U 列表的**本地浏览器**。所有流地址版权归原内容方所有，请勿用于商业用途。
