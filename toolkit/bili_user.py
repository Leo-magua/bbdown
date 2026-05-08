"""B 站 UP 主视频列表获取。

使用 B 站公开接口 `x/space/wbi/arc/search` 拉 UP 主最近投稿,需要 wbi
签名(w_rid + wts) + 登录态 Cookie。

**关键事实**:B 站 space API 不接受游客态,没有 SESSDATA 一定 412。
登录态来源按优先级:
  1. `BILI_COOKIE` 环境变量
  2. `BILI_COOKIES_TXT` 指向的 Netscape 格式文件 / `./cookies.txt`
  3. `BILI_STATE_PATH` 指向的 Playwright storage_state.json /
     `./storage_state.json`(跑 `python tools/bili_login.py` 扫码产出)

Cookie 有效期通常几个月,过期后重跑 tools/bili_login.py 刷新即可。
"""

from __future__ import annotations

import hashlib
import os
import time
import urllib.parse
from functools import reduce
from http.cookiejar import MozillaCookieJar
from typing import Any

import requests


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://space.bilibili.com",
    "Accept": "application/json, text/plain, */*",
}

# wbi mixin key 的字符重排顺序(来自社区逆向,固定表)
_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

_SESSION: requests.Session | None = None
_SESSION_INIT_AT: float = 0.0
_SESSION_TTL = 3600  # 1h 后刷新 buvid


def _load_user_cookies(session: requests.Session) -> bool:
    """Try to attach user-provided cookies. Returns True when any was added.

    Sources are tried in order; first non-empty one wins:
    - `BILI_COOKIE` env var: raw "key=val; key=val" string (e.g. from browser devtools)
    - `BILI_COOKIES_TXT` env var: path to Netscape-format cookies.txt
    - `./cookies.txt` next to the worker (auto-detected)
    - `BILI_STATE_PATH` env var: path to Playwright storage_state.json
    - `./storage_state.json` next to the worker (auto-detected)

    B 站 space API 不接受游客态 — 没有 SESSDATA 一定会 412。所以这里
    必须成功加载一个登录态,否则 get_up_videos 会失败。
    """
    added = False
    raw = os.getenv("BILI_COOKIE", "").strip()
    if raw:
        for part in raw.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                if k.strip():
                    session.cookies.set(k.strip(), v.strip(), domain=".bilibili.com")
                    added = True
        if added:
            return True

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    cookies_txt = os.getenv("BILI_COOKIES_TXT") or ""
    txt_candidates = [cookies_txt] if cookies_txt else []
    txt_candidates.append(os.path.join(here, "cookies.txt"))
    for path in txt_candidates:
        if path and os.path.isfile(path):
            try:
                jar = MozillaCookieJar()
                jar.load(path, ignore_discard=True, ignore_expires=True)
                for c in jar:
                    session.cookies.set(c.name, c.value, domain=c.domain or ".bilibili.com")
                    added = True
                if added:
                    return True
            except Exception:
                continue

    # Playwright storage_state.json(bili_login.py 产物)
    state_path = os.getenv("BILI_STATE_PATH") or ""
    state_candidates = [state_path] if state_path else []
    state_candidates.append(os.path.join(here, "storage_state.json"))
    for path in state_candidates:
        if path and os.path.isfile(path):
            try:
                import json
                with open(path, "r", encoding="utf-8") as f:
                    state = json.load(f)
                for c in state.get("cookies") or []:
                    domain = c.get("domain") or ""
                    if ".bilibili.com" not in domain:
                        continue
                    name = c.get("name")
                    value = c.get("value")
                    if name and value is not None:
                        session.cookies.set(name, value, domain=domain)
                        added = True
                if added:
                    return True
            except Exception:
                continue

    return added


def _new_session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update(DEFAULT_HEADERS)

    # 优先加载用户 Cookie(登录态能绕过大部分风控)
    used_user_cookie = _load_user_cookies(s)

    if not used_user_cookie:
        # 访问主页设置初始 cookies
        try:
            s.get("https://www.bilibili.com/", timeout=10, allow_redirects=True)
        except requests.RequestException:
            pass
        # finger/spi 拿正式 b_3 / b_4
        try:
            resp = s.get("https://api.bilibili.com/x/frontend/finger/spi", timeout=10)
            data = resp.json()
            if data.get("code") == 0:
                payload = data.get("data") or {}
                if payload.get("b_3"):
                    s.cookies.set("buvid3", payload["b_3"], domain=".bilibili.com")
                if payload.get("b_4"):
                    s.cookies.set("buvid4", payload["b_4"], domain=".bilibili.com")
        except (requests.RequestException, ValueError):
            pass
        if "buvid3" not in s.cookies.get_dict():
            import uuid
            s.cookies.set(
                "buvid3",
                f"{uuid.uuid4().hex[:16].upper()}-{uuid.uuid4().hex[:8].upper()}infoc",
                domain=".bilibili.com",
            )
        s.cookies.set("b_nut", str(int(time.time())), domain=".bilibili.com")
    return s


def _get_session() -> requests.Session:
    global _SESSION, _SESSION_INIT_AT
    now = time.time()
    if _SESSION is None or (now - _SESSION_INIT_AT) > _SESSION_TTL:
        _SESSION = _new_session()
        _SESSION_INIT_AT = now
    return _SESSION


_WBI_KEY: tuple[str, float] | None = None  # (mixin_key, cached_at)
_WBI_TTL = 3600


def _get_mixin_key(orig: str) -> str:
    return reduce(lambda s, i: s + orig[i], _MIXIN_KEY_ENC_TAB, "")[:32]


def _refresh_wbi_key() -> str:
    global _WBI_KEY
    session = _get_session()
    resp = session.get("https://api.bilibili.com/x/web-interface/nav", timeout=10)
    resp.raise_for_status()
    data = resp.json().get("data") or {}
    wbi = data.get("wbi_img") or {}
    img_url = wbi.get("img_url") or ""
    sub_url = wbi.get("sub_url") or ""
    img_key = img_url.rsplit("/", 1)[-1].split(".", 1)[0]
    sub_key = sub_url.rsplit("/", 1)[-1].split(".", 1)[0]
    mixin = _get_mixin_key(img_key + sub_key)
    _WBI_KEY = (mixin, time.time())
    return mixin


def _get_wbi_key() -> str:
    if _WBI_KEY is None or (time.time() - _WBI_KEY[1]) > _WBI_TTL:
        return _refresh_wbi_key()
    return _WBI_KEY[0]


def _sign_params(params: dict[str, Any]) -> dict[str, Any]:
    mixin = _get_wbi_key()
    params = dict(params)
    params["wts"] = int(time.time())
    sorted_items = sorted(params.items(), key=lambda x: x[0])
    # 去掉值里的特殊字符 "!'()*"
    sanitized = {
        k: str(v).translate({ord(c): None for c in "!'()*"})
        for k, v in sorted_items
    }
    query = urllib.parse.urlencode(sanitized, doseq=True)
    w_rid = hashlib.md5((query + mixin).encode("utf-8")).hexdigest()
    sanitized["w_rid"] = w_rid
    return sanitized


def _normalize_item(raw: dict[str, Any]) -> dict[str, Any]:
    bvid = raw.get("bvid") or raw.get("bv_id")
    created = raw.get("created") or raw.get("pubdate")
    pubdate_iso = None
    if isinstance(created, (int, float)) and created > 0:
        pubdate_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created))
    return {
        "bvid": bvid,
        "aid": raw.get("aid"),
        "title": (raw.get("title") or "").strip(),
        "description": raw.get("description") or raw.get("desc") or "",
        "cover_url": raw.get("pic"),
        "duration": raw.get("length"),
        "play_count": raw.get("play"),
        "comment_count": raw.get("comment"),
        "pubdate_ts": created,
        "pubdate": pubdate_iso,
        "video_url": f"https://www.bilibili.com/video/{bvid}" if bvid else None,
    }


def get_up_videos(mid: str | int, page_size: int = 30, page: int = 1) -> list[dict]:
    """Return the latest submissions of a UP user.

    Uses wbi-signed endpoint https://api.bilibili.com/x/space/wbi/arc/search

    **Expected failure mode**:B 站 space 接口受 IP/buvid 时间窗口风控,
    同一 IP 连续请求会被 412(-799/-352)拦住几十分钟。调用方应该当成
    临时错误 + 下次重试,不要当成永久性失败。
    """
    url = "https://api.bilibili.com/x/space/wbi/arc/search"
    raw_params: dict[str, Any] = {
        "mid": str(mid),
        "ps": page_size,
        "pn": page,
        "order": "pubdate",
        "platform": "web",
        "web_location": "1550101",
    }
    signed = _sign_params(raw_params)
    session = _get_session()

    def _call():
        resp = session.get(url, params=signed, timeout=15)
        resp.raise_for_status()
        return resp.json()

    data = _call()
    # 若 wbi 过期,刷新一次再试
    if data.get("code") in (-401, -352, -403):
        _refresh_wbi_key()
        data = _call()

    if data.get("code") != 0:
        raise RuntimeError(
            f"bilibili api error: code={data.get('code')} msg={data.get('message')}"
        )
    vlist = (((data.get("data") or {}).get("list") or {}).get("vlist")) or []
    return [_normalize_item(v) for v in vlist if v.get("bvid")]


def get_video_info(bvid: str) -> dict:
    """Fetch basic info for a single video."""
    url = "https://api.bilibili.com/x/web-interface/view"
    session = _get_session()
    resp = session.get(url, params={"bvid": bvid}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"bilibili api error: {data.get('message')}")
    raw = data.get("data") or {}
    return {
        "bvid": raw.get("bvid"),
        "aid": raw.get("aid"),
        "title": raw.get("title"),
        "description": raw.get("desc"),
        "cover_url": raw.get("pic"),
        "duration": raw.get("duration"),
        "play_count": (raw.get("stat") or {}).get("view"),
        "owner_mid": (raw.get("owner") or {}).get("mid"),
        "owner_name": (raw.get("owner") or {}).get("name"),
        "pubdate_ts": raw.get("pubdate"),
        "pubdate": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(raw["pubdate"]))
        if raw.get("pubdate")
        else None,
        "video_url": f"https://www.bilibili.com/video/{raw.get('bvid')}",
    }
