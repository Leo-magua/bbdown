# B 站 UP 主空间视频列表解析方案

> 目标页面：`https://space.bilibili.com/1023552416/upload/video`
> 分析日期：2026-05-05

---

## 一、背景

需要程序化获取 B 站 UP 主（mid=1023552416）的投稿视频列表。B 站 space 页面经历了多次风控升级，实际踩坑较多，本文记录完整排查过程和最终可用方案。

---

## 二、踩坑记录

### 2.1 方案一：直接解析 HTML（失败）

space 页面（`/upload/video`）已完全迁移为 **CSR（客户端渲染）**，服务端返回的 HTML 是一个空壳，没有任何视频数据：

```html
<div id="app" style="margin-top: -64px"><!--app-html--></div>
```

视频列表全靠前端 JS 调用 API 动态加载，直接 `requests.get()` 抓 HTML 拿不到任何东西。

### 2.2 方案二：调用 `x/space/wbi/arc/search` API（本地失败，服务器成功）

这是 B 站官方获取 UP 主投稿的接口，`toolkit/bili_user.py` 中已经实现了完整的 WBI 签名逻辑。但本地运行时返回了 **412 / -352 风控校验失败**。

**错误表现**：

```json
{"code": -352, "message": "风控校验失败", "ttl": 1}
```

最初误以为是 IP 被风控，于是去阿里云服务器测试，发现**服务器上可以正常返回数据**（Code: 0）。进一步排查后发现真正的原因不是"服务器 vs 本地"，而是以下两个条件的叠加。

### 2.3 关键发现 1：代理导致风控

本地环境变量中挂着代理：

```bash
ALL_PROXY=socks5://127.0.0.1:7890
```

当 `requests` 通过代理访问 B 站时，B 站会检测代理 IP 并返回 412 验证码页面。但 **`toolkit/bili_user.py` 中已经做了防护**——第 88 行设置了：

```python
s.trust_env = False
```

这会让 `requests.Session` 忽略系统代理，直接使用本地网络出口。所以**只要正确调用 `bili_user.py`，代理不会导致问题**。

### 2.4 关键发现 2：游客态直接被拒（真正的罪魁祸首）

真正导致本地第一次测试失败的原因是：**没有提供登录态 Cookie**。

`bili_user.py` 的 `_new_session()` 会优先读取环境变量 `BILI_COOKIE`，如果没有设置，则退化为游客态：

1. 访问 `https://www.bilibili.com/` 初始化 cookie
2. 调用 `x/frontend/finger/spi` 获取 `buvid3` / `buvid4`

但 B 站 `space/wbi/arc/search` 接口**不接受游客态**，无论 IP 是否干净，没有 `SESSDATA` 都会返回 412。

**验证结果**：

| 场景 | 结果 |
|------|------|
| 本地 + 游客态（无 Cookie）| ❌ 412 |
| 本地 + 登录态 Cookie + `trust_env=False` | ✅ 成功 30 条 |
| 阿里云 + 游客态 | ❌ -352 |
| 阿里云 + 登录态 Cookie | ✅ 成功 86 条 |

### 2.5 方案三：Playwright 浏览器渲染（失败）

尝试用 Playwright 打开 space 页面并等待渲染，但前端调用 `arc/search` 时同样被风控拦截。页面渲染后显示"什么都没有"的空状态。

浏览器日志中出现了 B 站风控系统的标志性请求：

```
POST https://api.bilibili.com/x/internal/gaia-gateway/ExClimbCongLing
```

这说明 B 站在前端层面也有风控校验，单纯浏览器自动化无法绕过。

### 2.6 方案四：搜索 API 兜底（可用但不完整）

```
GET https://api.bilibili.com/x/web-interface/search/type?keyword=大小马AI&search_type=video
```

搜索结果中包含 `mid` 字段，可以筛选出目标 UP 主的视频。实测本地能获取 20+ 条，但：

- 搜索结果可能不完整（依赖标题匹配）
- 翻页过多会触发限流

**仅推荐作为备选方案**。

---

## 三、根本原因总结

```
失败原因 ≠ "代码错误"
失败原因 ≠ "IP 被永久封禁"

真正原因：
  1. B 站 space API 必须登录态（SESSDATA）
  2. 本地代理会被风控，但代码已设置 trust_env=False 规避
  3. 第一次测试时没配 Cookie，走了游客态 → 412
```

---

## 四、最终方案

**使用 `toolkit/bili_user.py` 中的 `get_up_videos()`，配合登录态 Cookie 调用。**

### 4.1 Cookie 来源

1. **浏览器 DevTools**：登录 B 站后，在 Console 执行 `document.cookie`，复制完整字符串
2. **`storage_state.json`**：如果之前用 Playwright 登录过，从该文件中提取 `SESSDATA`、`bili_jct`、`DedeUserID` 等关键字段

### 4.2 环境变量配置

```bash
# 方式一：直接设置环境变量
export BILI_COOKIE="SESSDATA=xxx; bili_jct=xxx; DedeUserID=xxx; buvid3=xxx; buvid4=xxx"

# 方式二：保存为 cookies.txt（Netscape 格式）
export BILI_COOKIES_TXT="/path/to/cookies.txt"
```

### 4.3 完整示例代码

见 [`examples/fetch_up_videos.py`](../examples/fetch_up_videos.py)

### 4.4 运行效果

```
Fetching page 1... got 30 videos, total: 30
Fetching page 2... got 30 videos, total: 60
Fetching page 3... got 26 videos, total: 86
Done! Total 86 videos saved to up_1023552416_videos.json
```

---

## 五、注意事项

1. **Cookie 有效期**：`SESSDATA` 有过期时间（通常是几个月），过期后需要重新获取
2. **请求间隔**：翻页时建议间隔 1-2 秒，避免触发频率限制
3. **代理**：如果系统挂着代理，确保代码中设置 `session.trust_env = False`，`bili_user.py` 已内置
4. **风控容忍**：B 站风控策略随时可能调整，调用方应做好异常处理，不要把临时失败当成永久性错误

---

## 六、相关文件

| 文件 | 说明 |
|------|------|
| `toolkit/bili_user.py` | 核心模块，提供 `get_up_videos()` 和 `get_video_info()` |
| `examples/fetch_up_videos.py` | 完整可运行的抓取示例 |
| `examples/fetch_up_videos_from_storage.py` | 从 `storage_state.json` 读取 Cookie 的示例 |
