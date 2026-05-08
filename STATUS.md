# bili-worker 状态盘点(2026-05-05)

## 架构定位

**bili-worker 是数据后端,cognihub 是前端。**
后端**自闭环**:定时扫订阅 → 拉新 bvid → 下载 → 转写 → 写入本地
sqlite。前端只读 `/api/subscriptions` 和 `/api/videos`,不再主动调度。

实际形态:
- 本机 `:5070` Flask 服务 + 后台 daemon 调度线程,内网/tailscale 暴露
- 状态库:`./data/bili-worker.db` (sqlite3 + WAL),订阅与视频在同一库
- cognihub 用 `BILIBILI_API_BASE=http://100.123.114.96:5070` 调本服务

## 能力矩阵

### 老接口(同步,不写库)

| 端点 | 状态 | 说明 |
|---|---|---|
| `GET  /api/health` | ✅ | 心跳 |
| `GET  /api/bvid/<bvid>/info` | ✅ | 单视频元数据 |
| `POST /api/download` + `GET /api/status/<task_id>` | ✅ | 同步触发 + 内存轮询 |
| `POST /api/transcribe` + `GET /api/transcribe/status/<task_id>` | ✅ | 同上 |
| `GET  /api/up/<mid>/videos` | ✅(需登录态) | 实时打 B 站,旁路 db |

### 新接口(异步闭环)

| 端点 | 行为 |
|---|---|
| `GET  /api/subscriptions` | 列订阅 |
| `POST /api/subscriptions` body `{mid, name, scan_interval_hours?, is_active?}` | 加/更新订阅 |
| `DELETE /api/subscriptions/<mid>` | 删订阅 |
| `POST /api/subscriptions/<mid>/scan-now` | 立即扫(给 cognihub 加塞热点 mid) |
| `GET  /api/videos?mid=&download_status=&transcription_status=&limit=&offset=` | 列视频 |
| `GET  /api/videos/<bvid>` | 单视频(含 transcription/timestamped_text/segments) |
| `GET  /api/scheduler/tick` | 立即跑一轮调度(运维/调试用) |

## 数据库

`./data/bili-worker.db`:

- `subscriptions(mid PK, name, is_active, scan_interval_hours, last_checked_at, last_attempt_at, last_error, last_video_bvid, ...)`
- `videos(bvid PK, mid, title, pubdate, pubdate_ts, duration, ..., download_status, transcription_status, transcription, timestamped_text, segments_json, language, audio_duration, ...)`
- 索引:`(mid, pubdate_ts)`、`download_status`、`transcription_status`

字段名跟 cognihub 的 ORM model 对齐,后续若想合并到一个库零成本。

## 闭环工作流程

```
启动 Flask
  ├── init_db() 建表
  └── scheduler.start() 后台线程

scheduler.tick() 每 60s:
  1. 主扫:每个 active sub,看 last_attempt_at,到 12h 的扫
       └── get_up_videos(mid) → upsert_video → submit_download(前 3 个新 bvid)
  2. backlog_downloads:状态 'pending' 的 video 重试入队
  3. backlog_transcribes:状态 'done' 但未转写的 video 入转写队列
       └── 包括手工 cp 进 ./data/downloads/<bvid>/ 的视频(scheduler 自动发现)

worker._download_job(bvid) → submit_transcribe(bvid)  # 链式
worker._transcribe_job(bvid) → store.update_video_transcription
```

## 转写格式

`videos.timestamped_text` 形如:

```
[00:00.000 → 00:03.520] 警惕 兄弟们对于通信对于海外算力
[00:03.800 → 00:08.120] 接下来我们要警惕了 在4月22日当天的视频...
```

`videos.segments_json` 是 `[{start, end, start_formatted, end_formatted, text}, ...]` 的 JSON。`/api/videos/<bvid>` 返回时会自动解 JSON 成 `segments` 字段。

实现位置:`toolkit/bili_transcriber.py` 的 `TranscriptResult.to_payload()` + `merged(min_duration=3.0)`(借鉴 v1,合并 < 3s 短段)。

## 主流程

### 自动闭环(默认)

cognihub 那边:写 `up_subscriptions` 表 → bili-worker 这边
`tools/bootstrap_subscriptions.py --ssh macmini --remote-path ...` 同步过来 → 启动
worker → 自动跑。

### 手工触发(临时调用)

cognihub 给一个 bvid → `POST /api/download` → 完成自动转写 → 写库。

## 登录态(B 站 cookie)

`toolkit/bili_user.py` 优先级查找:
1. `BILI_COOKIE` 环境变量
2. `BILI_COOKIES_TXT` / `./cookies.txt`
3. `BILI_STATE_PATH` / `./storage_state.json` ← **当前用这个**

生成/刷新:`source .venv/bin/activate && python tools/bili_login.py`(扫码,几个月一次)。

## 2026-05-05 踩的坑(按时间)

### 坑 1:pip 安装被 SOCKS 代理阻塞

`ALL_PROXY=socks5://127.0.0.1:7890`,pip 不识别。`unset ALL_PROXY ... && pip install` 解决。

### 坑 2:playwright sync_api 的线程绑定

playwright 对象绑创建它的线程,跨线程调用抛
`cannot switch to a different thread`。如果以后用,submit 到
`ThreadPoolExecutor(max_workers=1)`。

### 坑 3:把"v1 跑通过"错记成"UP 列表跑通过"

v1 只有关键词搜索,从来没做过 UP 列表。但 cognihub 数据库里有早上 06:21 的成功
扫描记录 — 那是**带 cookie 的 wbi 路径成功**。

### 坑 4:把"登录态问题"错判成"IP 级风控"(本日最大坑)

一上午折腾 playwright / aliyun / stealth,**正解早就在代码里**:`BILI_COOKIE` +
`trust_env=False`。我整天都没加 Cookie 跑。**B 站 space API 不接受游客态,
没 SESSDATA 一定 412**,跟 IP 干净不干净无关。kimi 跑通的 POC 一句话点醒。

### 坑 5:代理不影响 worker 本身

`session.trust_env = False` 已经忽略环境代理。

## 后续 TODO

1. ~~**cognihub `up_monitor` 弃用**~~ — **已完成 2026-05-05**:cognihub
   新增 `services/up_sync.py`,改读 `/api/videos`。详见
   `.trash/poc-attempts/cognihub-patch/README.md`。
2. ~~**mac-rui 远程转写节点**~~ — **已完成 2026-05-05**:`BILI_REMOTE_TRANSCRIBE=mac-rui`
   开启,`toolkit/remote_transcriber.py` 通过 SSH+scp 把任务发到第二台
   macbook 跑,失败自动降级本地。`_transcribe_pool` 扩容到 2 worker
   (本地 + 远程并发)。`_remote_lock.acquire(blocking=False)`:远程已被
   另一个线程占用时立刻抛 "remote busy" 让上层降级,避免两个 worker 都
   堵在锁上。timeout 默认 7200s(2h),可 `BILI_REMOTE_TIMEOUT` 覆盖。
   详见 `.trash/poc-attempts/mac-rui-setup/README.md`。
3. ~~**cognihub 前端切页面慢**~~ — **已完成 2026-05-05**:`App.tsx`
   改成全 mount + `display: none` 隐藏,新增 `hooks/queries.ts` 提供
   react-query 缓存层。切 tab 不再 unmount,体验秒切。
4. ~~**cognihub 三页重构 + 事件抽象 + LLM provider 切换**~~ — **已完成 2026-05-05**:
   - 5 个 tab 合并为 3 个(看新内容/整理笔记/信息源)
   - 新增 Event/EventSource 表 + `routes/events.py` + `services/event_store.py`
     双路存储(DB + `data/events/<slug>.md`)
   - 新增 `services/llm_link.py` LLM 联动:每次新增 source 自动跟所有
     active event 比对(开关 `auto_link_events`),手动也可点事件卡片"联动"按钮
   - LLM provider 切换:settings 同时存 DeepSeek + StepFun key,顶部
     ProviderSwitcher 一键切换。`config.get_provider_config(provider)` /
     `call_llm(..., provider=...)`
   - 笔记功能:`routes/notes.py` + `data/notes/<slug>_<id>.md` 文件存储,
     前端 `NotesPage` textarea + marked 实时预览,2s 防抖自动保存
   - 视频转写在事件详情里用 `<pre whitespace-pre-wrap>` 渲染 timestamped_text,
     不再堆一坨
5. **`_download_job` 忽略 `media_type`**(`worker.py:91`):前端传
   `"audio"`/`"video"` 都跑 you-get 默认下完整 mp4。优先级低。
6. **Cookie 过期检测 + 退避**:scheduler tick 失败时,如果 `last_error`
   持续是 `code=-101 账号未登录`,在 health 接口标红 + 自动 `is_active=0`
   等人重新扫码。
7. **`/api/search?keyword=...`**:复活 `toolkit/bili_crawler.py` 的关键词
   搜索能力,作为 UP 订阅之外的备选发现路径。
8. **新闻门户接入**:cognihub Sources 页面已留位,后期增加。

## 任务幂等设计(2026-05-05 加固)

`videos` 表新增中间状态 `queued`,`download_status` / `transcription_status`
现在的合法值为 `pending → queued → running → done|error`。

- `submit_download/transcribe` 进函数立刻 `claim_video_for_*`
  (UPDATE...WHERE status IN ('pending','error')),抢锁失败的(已是
  queued/running/done)直接返回 skipped Task。
- 老的内存 `_find_existing` 仍在(给同进程内重复 submit 用),sqlite
  抢锁是给跨进程 / 进程重启后场景。
- 启动时 `reset_stale_active_states()` 把 stale 的
  `queued`/`running` 全部刷回 `pending`,scheduler 下一轮重新认领。
- `videos_pending_transcribe()` 只返回 `pending`,不再返回 `error`(避免
  无限重试 you-get oops 那类视频)。手工恢复:`UPDATE videos SET
  transcription_status='pending' WHERE bvid='BV...' AND
  transcription_status='error'`。

## 文件结构(当前)

```
bili-worker/
├── app.py                       # Flask 路由 + 启动 init_db / start_scheduler
├── worker.py                    # 下载/转写任务 + 双写 store
├── requirements.txt             # flask, requests, bs4, openai-whisper
├── storage_state.json           # B 站登录态(几个月过期)
├── STATUS.md
├── README.md
├── docs/
│   └── bilibili-space-api-analysis.md   # kimi 的踩坑分析
├── examples/
│   ├── fetch_up_videos.py
│   └── fetch_up_videos_from_storage.py
├── tools/
│   ├── bili_login.py            # 扫码登录,生成 storage_state.json
│   └── bootstrap_subscriptions.py  # 从 cognihub.db 同步订阅(一次性)
├── toolkit/
│   ├── bili_user.py             # wbi 签名 + cookie 加载 + get_up_videos
│   ├── bili_downloader.py       # you-get 封装
│   ├── bili_transcriber.py      # Whisper + timestamped + merge_short_segments
│   ├── bili_crawler.py          # 关键词搜索(可用,未 HTTP 暴露)
│   ├── db.py                    # sqlite3 连接 + WAL + init_db
│   ├── store.py                 # 业务对 db 的 CRUD 封装
│   └── scheduler.py             # 后台调度器(daemon thread, 60s tick)
├── data/
│   ├── bili-worker.db           # 本地 sqlite,subscriptions + videos
│   └── downloads/<bvid>/        # 下载产物
└── .trash/                      # 废弃尝试归档(playwright 路线)
    ├── README.md
    ├── bili_space_browser.py
    ├── bili_login.py            # (已复制到 tools/,留备份)
    ├── storage_state.json       # (已复制到根,留备份)
    └── poc-attempts/
        ├── codex/               # codex 的 RSSHub 路线
        └── kimi/                # kimi 的登录态路线 = 真正解决
```

