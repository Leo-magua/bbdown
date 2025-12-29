# backend/crawler.py
"""
B站视频搜索爬虫模块
"""
import requests
from bs4 import BeautifulSoup
import re
import time
import random
from urllib.parse import quote
from datetime import datetime
from typing import List, Dict, Optional, Callable


class BilibiliCrawler:
    """B站视频搜索爬虫"""

    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Referer': 'https://www.bilibili.com',
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)

    def search(self, keyword: str, page: int = 1) -> List[Dict]:
        """
        搜索B站视频

        Args:
            keyword: 搜索关键词
            page: 页码

        Returns:
            视频信息列表
        """
        encoded_keyword = quote(keyword, encoding='utf-8')

        if page == 1:
            url = f"https://search.bilibili.com/all?keyword={encoded_keyword}"
        else:
            offset = (page - 1) * 30
            url = f"https://search.bilibili.com/all?keyword={encoded_keyword}&page={page}&o={offset}"

        try:
            response = self.session.get(url, timeout=15)
            if response.status_code == 200:
                return self._parse_search_results(response.text)
            else:
                print(f"搜索请求失败，状态码: {response.status_code}")
                return []
        except Exception as e:
            print(f"搜索失败: {e}")
            return []

    def _parse_search_results(self, html_content: str) -> List[Dict]:
        """解析搜索结果页面"""
        videos = []

        try:
            # 从HTML中提取完整标题
            bvid_title_map = self._extract_titles_from_html(html_content)

            soup = BeautifulSoup(html_content, 'html.parser')
            scripts = soup.find_all('script')

            for script in scripts:
                if not script.string:
                    continue

                script_content = script.string

                if 'type:f' in script_content and 'bvid:' in script_content:
                    video_blocks = self._extract_video_blocks(script_content)

                    for block in video_blocks:
                        video_info = self._parse_video_object(block)
                        if video_info:
                            bvid = video_info.get('bvid', '')
                            if bvid in bvid_title_map:
                                video_info['title'] = bvid_title_map[bvid]
                            videos.append(video_info)
                    break

        except Exception as e:
            print(f"解析搜索结果失败: {e}")

        return videos

    def _extract_titles_from_html(self, html_content: str) -> Dict[str, str]:
        """从HTML中提取BVID到标题的映射"""
        bvid_title_map = {}

        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            video_cards = soup.find_all('div', class_='bili-video-card__info--right')

            for card in video_cards:
                try:
                    link = card.find('a', href=True)
                    if link:
                        href = link.get('href', '')
                        bvid_match = re.search(r'/video/(BV[a-zA-Z0-9]+)/', href)
                        if bvid_match:
                            bvid = bvid_match.group(1)
                            title_elem = card.find('h3', class_='bili-video-card__info--tit')
                            if title_elem:
                                title = title_elem.get_text(strip=True)
                                if title:
                                    bvid_title_map[bvid] = title
                except:
                    continue

        except Exception as e:
            print(f"提取标题失败: {e}")

        return bvid_title_map

    def _extract_video_blocks(self, script_content: str) -> List[str]:
        """从JavaScript代码中提取视频数据块"""
        video_blocks = []

        try:
            for match in re.finditer(r'\{type:f,', script_content):
                start_pos = match.start()
                block = self._extract_complete_object(script_content, start_pos)
                if block:
                    video_blocks.append(block)
        except Exception as e:
            print(f"提取视频块失败: {e}")

        return video_blocks

    def _extract_complete_object(self, content: str, start_pos: int) -> Optional[str]:
        """从指定位置提取完整的JavaScript对象"""
        try:
            if start_pos >= len(content) or content[start_pos] != '{':
                return None

            brace_count = 0
            pos = start_pos
            in_quotes = False
            escape_next = False

            while pos < len(content):
                char = content[pos]

                if char == '\\' and not escape_next:
                    escape_next = True
                    pos += 1
                    continue

                if char == '"' and not escape_next:
                    in_quotes = not in_quotes
                elif not in_quotes:
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            return content[start_pos:pos + 1]

                escape_next = False
                pos += 1

            return None
        except:
            return None

    def _parse_video_object(self, obj_str: str) -> Optional[Dict]:
        """解析单个视频对象字符串"""
        try:
            video_info = {}

            fields = {
                'bvid': r'bvid:"([^"]*)"',
                'title': r'title:"([^"]*)"',
                'description': r'description:"([^"]*)"',
                'arcurl': r'arcurl:"([^"]*)"',
                'play': r'play:(\d+)',
                'review': r'review:(\d+)',
                'tag': r'tag:"([^"]*)"',
                'pubdate': r'pubdate:(\d+)',
                'duration': r'duration:"([^"]*)"',
            }

            for field, pattern in fields.items():
                match = re.search(pattern, obj_str)
                if match:
                    value = match.group(1)
                    if field in ['play', 'review', 'pubdate']:
                        video_info[field] = int(value) if value.isdigit() else 0
                    else:
                        video_info[field] = self._decode_js_string(value)
                else:
                    video_info[field] = '' if field not in ['play', 'review', 'pubdate'] else 0

            video_info['author'] = ''
            video_info['uploadDate'] = ''

            # 转换时间戳
            if video_info.get('pubdate') and isinstance(video_info.get('pubdate'), int):
                video_info['pubdate'] = self._timestamp_to_datetime(video_info['pubdate'])

            if video_info.get('bvid') and video_info.get('bvid').startswith('BV'):
                return video_info

        except Exception as e:
            print(f"解析视频对象失败: {e}")

        return None

    def _decode_js_string(self, s: str) -> str:
        """解码JavaScript字符串中的转义字符"""
        try:
            s = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), s)

            replacements = {
                '\\n': '\n', '\\r': '\r', '\\t': '\t',
                '\\"': '"', '\\\\': '\\', '\\u002F': '/',
                '\\u003C': '<', '\\u003E': '>', '\\u0026': '&'
            }

            for old, new in replacements.items():
                s = s.replace(old, new)

            return s
        except:
            return s

    def _timestamp_to_datetime(self, timestamp: int) -> str:
        """将时间戳转换为标准时间格式"""
        try:
            if timestamp and timestamp > 0:
                dt = datetime.fromtimestamp(timestamp)
                return dt.strftime('%Y-%m-%d %H:%M:%S')
            return ''
        except:
            return ''

    def get_video_detail(self, url: str) -> Optional[Dict]:
        """获取视频详细信息"""
        try:
            response = self.session.get(url, timeout=10)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')

                title_tag = soup.find('meta', {'itemprop': 'name'})
                author_tag = soup.find('meta', {'itemprop': 'author'})
                upload_date_tag = soup.find('meta', {'itemprop': 'uploadDate'})
                publish_date_tag = soup.find('meta', {'itemprop': 'datePublished'})
                desc_tag = soup.find('meta', {'itemprop': 'description'})

                title = title_tag.get('content', '') if title_tag else ''
                title = title.replace('_哔哩哔哩_bilibili', '')

                author = author_tag.get('content', '') if author_tag else ''
                upload_date = upload_date_tag.get('content', '') if upload_date_tag else ''
                publish_date = publish_date_tag.get('content', '') if publish_date_tag else ''

                full_desc = desc_tag.get('content', '') if desc_tag else ''
                if '视频播放量' in full_desc:
                    description = full_desc.split('视频播放量')[0].strip()
                else:
                    description = full_desc.strip()
                if description.endswith(','):
                    description = description[:-1].strip()

                return {
                    'title': title,
                    'author': author,
                    'description': description,
                    'uploadDate': upload_date,
                    'datePublished': publish_date
                }
            else:
                return None
        except Exception as e:
            print(f"获取视频详情失败: {e}")
            return None

    def enrich_videos(self, videos: List[Dict],
                      progress_callback: Optional[Callable] = None) -> List[Dict]:
        """补充视频详细信息"""
        enriched_videos = []
        total = len(videos)

        for i, video in enumerate(videos):
            url = video.get('arcurl', '')
            if url:
                detailed_info = self.get_video_detail(url)

                if detailed_info:
                    video['title'] = detailed_info['title']
                    video['description'] = detailed_info['description']
                    video['author'] = detailed_info['author']
                    video['uploadDate'] = detailed_info['uploadDate']

                    if detailed_info['datePublished']:
                        video['pubdate'] = detailed_info['datePublished']

                time.sleep(random.uniform(0.5, 1.5))

            enriched_videos.append(video)

            if progress_callback and (i + 1) % 10 == 0:
                progress_callback(f"已处理 {i + 1}/{total} 个视频")

        return enriched_videos

