# bili-worker

本机运行的 B 站视频下载 + Whisper 转写小服务。被 macmini 上的 cognihub 通过 tailscale 调用。

## 启动

```bash
source .venv/bin/activate
python app.py
# 监听 0.0.0.0:5070
```

首次启动会下载 Whisper medium 模型到 `~/.cache/whisper/`（~769MB），之后常驻内存。
启动时 worker 会自动:
- 建表 `data/bili-worker.db`(subscriptions + videos)
- 启动后台调度线程(60s tick,12h 一轮扫订阅)

## 同步订阅(从 cognihub 一次性 import)

```bash
python tools/bootstrap_subscriptions.py --ssh macmini --remote-path \
    /Users/wendy/AllProject/cognihub/backend/cognihub.db
```

## 端点

### 自动化闭环(推荐)
- `POST /api/subscriptions` body `{"mid":"123","name":"xxx","scan_interval_hours":12}` — 加订阅,12h 自动扫
- `GET  /api/subscriptions` — 列订阅
- `DELETE /api/subscriptions/<mid>` — 删订阅
- `POST /api/subscriptions/<mid>/scan-now` — 立即扫(给热点 mid 加塞)
- `GET  /api/videos?mid=&download_status=&transcription_status=&limit=` — 列视频(已入库)
- `GET  /api/videos/<bvid>` — 单视频(含 `transcription` / `timestamped_text` / `segments`)
- `GET  /api/scheduler/tick` — 立即跑一轮调度(运维/调试)

### 老接口(同步,不写库)
- `GET  /api/health`
- `GET  /api/up/<mid>/videos?limit=30` — 实时打 B 站,不入库
- `GET  /api/bvid/<bvid>/info` — 单视频元数据
- `POST /api/download` + `GET /api/status/<task_id>`
- `POST /api/transcribe` + `GET /api/transcribe/status/<task_id>`

## UP 主列表抓取(需登录态)

走 `x/space/wbi/arc/search` + wbi 签名。**B 站 space API 不接受游客态,
没 SESSDATA 一定 412**。成功时 < 500ms 返回列表,支持分页。

### 配登录态

优先级从高到低,找到第一个就用:

1. `BILI_COOKIE` 环境变量(浏览器 DevTools `document.cookie` 复制)
2. `BILI_COOKIES_TXT` 指向的 Netscape 格式文件 / `./cookies.txt`
3. `BILI_STATE_PATH` / `./storage_state.json` — **推荐**

最简单的办法:

```bash
source .venv/bin/activate
python tools/bili_login.py
# 弹出带头浏览器 → 右上角登录 → B 站 App 扫码
# 看到头像后回终端按回车 → 落盘到 ./storage_state.json
```

之后起 worker 会自动加载。Cookie 过期后(几个月)重跑即可。

### 参考示例

- `examples/fetch_up_videos.py` — 独立脚本,用 `BILI_COOKIE` 环境变量
- `examples/fetch_up_videos_from_storage.py` — 从 `storage_state.json`
  提取 Cookie
- `docs/bilibili-space-api-analysis.md` — kimi 写的完整踩坑分析

## 下载产物

落盘在 `./data/downloads/<bvid>/`。

## 从 cognihub 访问

macmini 上环境变量：

```bash
export BILIBILI_API_BASE=http://100.123.114.96:5070
```
