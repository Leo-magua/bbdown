"""
B站关键词搜索爬虫

核心经验：
1. B站搜索页已从旧的内联 JS 对象格式迁移到 window.__pinia 混淆格式
2. 变量名被压缩，但属性名（bvid, title, play 等）仍是字符串，可用正则提取
3. 云服务器 IP 极易被封锁，住宅 IP 相对安全
4. 请求间隔建议 3-5 秒/页
"""

import requests
import re
import time
import random
from bs4 import BeautifulSoup
from urllib.parse import quote
from datetime import datetime
from typing import List, Dict, Optional


HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.8',
    'Referer': 'https://www.bilibili.com',
}


def search_bilibili_videos(keyword: str, page: int = 1, session: Optional[requests.Session] = None) -> List[Dict]:
    """
    搜索B站视频，返回视频列表

    Args:
        keyword: 搜索关键词
        page: 页码（从1开始）
        session: 可选的 requests.Session，用于保持 Cookie

    Returns:
        视频信息列表，每个元素包含 bvid, title, play, duration, arcurl, pubdate, tag
    """
    encoded = quote(keyword, encoding='utf-8')
    url = f"https://search.bilibili.com/all?keyword={encoded}"
    if page > 1:
        url += f"&page={page}&o={(page - 1) * 30}"

    sess = session or requests
    resp = sess.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    # 简单验证：正常页面约 300KB+，被封约 3KB
    if len(resp.text) < 10000:
        print(f"警告: 页面过小 ({len(resp.text)} 字节)，可能 IP 被封锁")
        return []

    return parse_search_page(resp.text)


def parse_search_page(html: str) -> List[Dict]:
    """
    解析B站搜索页 HTML，提取视频信息

    策略：
    1. 从 DOM 提取标题作为补充/校正
    2. 从 window.__pinia 脚本中全局正则匹配 bvid，再通过上下文窗口提取其他字段
    """
    videos = []
    soup = BeautifulSoup(html, 'html.parser')

    # --- 步骤1：从 DOM 提取标题（备用/补充）---
    bvid_title_map = {}
    for card in soup.find_all('div', class_='bili-video-card__info--right'):
        link = card.find('a', href=True)
        if not link:
            continue
        bvid_match = re.search(r'/video/(BV[a-zA-Z0-9]+)/', link.get('href', ''))
        if bvid_match:
            bvid = bvid_match.group(1)
            title_elem = card.find('h3', class_='bili-video-card__info--tit')
            if title_elem:
                bvid_title_map[bvid] = title_elem.get_text(strip=True)

    # --- 步骤2：从 window.__pinia 提取完整数据 ---
    for script in soup.find_all('script'):
        if not script.string or 'window.__pinia' not in script.string:
            continue

        text = script.string
        bvid_matches = list(re.finditer(r'bvid:"(BV[a-zA-Z0-9]+)"', text))

        for m in bvid_matches:
            bvid = m.group(1)
            pos = m.start()
            # 上下文窗口：前后各 400 字符
            ctx = text[max(0, pos - 400):pos + 400]

            # play 可能是 play:12345 或 play:"12345"
            play_match = re.search(r'play[:"](\d+)', ctx)
            duration_match = re.search(r'duration:"([^"]+)"', ctx)
            arcurl_match = re.search(r'arcurl:"([^"]+)"', ctx)
            pubdate_match = re.search(r'pubdate:(\d+)', ctx)
            tag_match = re.search(r'tag:"([^"]+)"', ctx)

            video = {
                'bvid': bvid,
                'title': bvid_title_map.get(bvid, ''),
                'play': int(play_match.group(1)) if play_match else 0,
                'duration': duration_match.group(1) if duration_match else '',
                'arcurl': arcurl_match.group(1) if arcurl_match else '',
                'pubdate': int(pubdate_match.group(1)) if pubdate_match else 0,
                'tag': tag_match.group(1) if tag_match else '',
            }
            videos.append(video)
        break

    return videos


def get_video_detail(url: str, session: Optional[requests.Session] = None) -> Optional[Dict]:
    """
    访问视频详情页，补充标题、作者、简介、发布时间等

    从 HTML meta 标签提取：
    - meta[itemprop="name"] -> title
    - meta[itemprop="author"] -> author
    - meta[itemprop="uploadDate"] -> uploadDate
    - meta[itemprop="description"] -> description
    """
    sess = session or requests
    try:
        resp = sess.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.content, 'html.parser')

        title_tag = soup.find('meta', {'itemprop': 'name'})
        author_tag = soup.find('meta', {'itemprop': 'author'})
        upload_date_tag = soup.find('meta', {'itemprop': 'uploadDate'})
        desc_tag = soup.find('meta', {'itemprop': 'description'})

        title = title_tag.get('content', '') if title_tag else ''
        title = title.replace('_哔哩哔哩_bilibili', '')

        full_desc = desc_tag.get('content', '') if desc_tag else ''
        if '视频播放量' in full_desc:
            description = full_desc.split('视频播放量')[0].strip()
        else:
            description = full_desc.strip()
        if description.endswith(','):
            description = description[:-1].strip()

        return {
            'title': title,
            'author': author_tag.get('content', '') if author_tag else '',
            'uploadDate': upload_date_tag.get('content', '') if upload_date_tag else '',
            'description': description,
        }
    except Exception as e:
        print(f"获取视频详情失败: {e}")
        return None


def enrich_videos(videos: List[Dict], delay_range: tuple = (0.5, 1.5)) -> List[Dict]:
    """
    为视频列表补充详情页信息（标题、作者、简介等）

    Args:
        videos: 视频列表
        delay_range: 每个请求之间的随机延迟（秒）

    Returns:
        补充信息后的视频列表
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    for video in videos:
        url = video.get('arcurl', '')
        if not url:
            continue

        detail = get_video_detail(url, session=session)
        if detail:
            video['title'] = detail['title'] or video.get('title', '')
            video['author'] = detail['author']
            video['description'] = detail['description']
            video['uploadDate'] = detail['uploadDate']

        time.sleep(random.uniform(*delay_range))

    return videos


def batch_search(
    keywords: List[str],
    pages_per_keyword: int = 3,
    delay_range: tuple = (3.0, 5.0),
    enrich: bool = True,
) -> List[Dict]:
    """
    批量搜索多个关键词

    Args:
        keywords: 关键词列表
        pages_per_keyword: 每个关键词搜索的页数
        delay_range: 每页之间的随机延迟（秒）
        enrich: 是否补充详情页信息

    Returns:
        所有视频列表（已去重）
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    all_videos = []
    for keyword in keywords:
        print(f"搜索: {keyword}")
        for page in range(1, pages_per_keyword + 1):
            videos = search_bilibili_videos(keyword, page=page, session=session)
            if videos:
                for v in videos:
                    v['搜索关键词'] = keyword
                    v['操作时间'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                all_videos.extend(videos)
                print(f"  第{page}页: {len(videos)} 个")
            else:
                print(f"  第{page}页: 无数据")

            time.sleep(random.uniform(*delay_range))

    # 去重
    seen = set()
    unique = []
    for v in all_videos:
        bvid = v.get('bvid')
        if bvid and bvid not in seen:
            seen.add(bvid)
            unique.append(v)

    print(f"去重前: {len(all_videos)}, 去重后: {len(unique)}")

    if enrich and unique:
        unique = enrich_videos(unique)

    return unique


# ========== 命令行测试 ==========
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python bili_crawler.py <keyword> [pages]")
        print("Example: python bili_crawler.py Python教程 2")
        sys.exit(1)

    keyword = sys.argv[1]
    pages = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    results = batch_search([keyword], pages_per_keyword=pages, enrich=False)
    print(f"\n共获取 {len(results)} 个视频:")
    for v in results[:5]:
        print(f"  {v['bvid']}: {v['title'][:40]}... 播放{v['play']}")
