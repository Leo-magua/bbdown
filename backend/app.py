# backend/app.py
"""
B站视频信息爬取、下载与AI总结工具 - 后端服务
"""
import os
import subprocess
import json
import threading
import traceback
import re
import time
import random
from datetime import datetime
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename
import pandas as pd
from openai import OpenAI

# 导入自定义模块
from crawler import BilibiliCrawler
from transcriber import get_transcriber, TranscriptResult

# ========== 配置 ==========
os.environ['PATH'] = '/opt/homebrew/bin:/usr/local/bin:' + os.environ.get('PATH', '')

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, 'frontend')
DOWNLOAD_DIR = os.path.join(BASE_DIR, 'downloads')
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')

# 创建Flask应用
app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path='')
CORS(app, supports_credentials=True, resources={r"/*": {"origins": "*"}})

app.config['UPLOAD_FOLDER'] = UPLOAD_DIR
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB
app.config['ALLOWED_EXTENSIONS'] = {'xlsx', 'xls'}

# 确保目录存在
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ========== 全局状态存储 ==========
# 下载任务状态
download_task_status = {}

# 转写任务状态
transcribe_status = {}

# 爬虫任务状态
crawler_status = {
    'is_running': False,
    'is_paused': False,
    'progress': 0,
    'current_task': '',
    'total_keywords': 0,
    'processed_keywords': 0,
    'total_videos': 0,
    'current_keyword': '',
    'error': None,
    'logs': [],
    'videos': []
}

# 初始化爬虫
crawler = BilibiliCrawler()


# ========== 工具函数 ==========
def allowed_file(filename):
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']


def add_crawler_log(message, is_error=False):
    """添加爬虫日志"""
    timestamp = datetime.now().strftime('%H:%M:%S')
    log_entry = {
        'timestamp': timestamp,
        'message': message,
        'is_error': is_error
    }
    crawler_status['logs'].append(log_entry)
    if len(crawler_status['logs']) > 100:
        crawler_status['logs'] = crawler_status['logs'][-100:]


def read_keywords(filepath):
    """读取关键词Excel文件"""
    try:
        df = pd.read_excel(filepath)
        keywords = df['item'].tolist()
        return [str(keyword).strip() for keyword in keywords if pd.notna(keyword)]
    except Exception as e:
        print(f"读取关键词文件失败: {e}")
        return []


# ========== 爬虫任务 ==========
def run_crawler_task(filename, pages_per_keyword=5, enable_detailed_info=True, remove_duplicates=True):
    """运行爬虫任务"""
    global crawler_status

    try:
        crawler_status['is_running'] = True
        crawler_status['is_paused'] = False
        crawler_status['progress'] = 0
        crawler_status['error'] = None
        crawler_status['logs'] = []
        crawler_status['videos'] = []

        # 读取关键词
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        keywords = read_keywords(filepath)
        if not keywords:
            crawler_status['error'] = "未找到关键词"
            add_crawler_log("未找到关键词", True)
            return

        crawler_status['total_keywords'] = len(keywords)
        crawler_status['processed_keywords'] = 0
        crawler_status['total_videos'] = 0

        add_crawler_log(f"找到 {len(keywords)} 个关键词")

        all_videos = []

        # 第一阶段：搜索并抓取基础信息
        for i, keyword in enumerate(keywords):
            while crawler_status['is_paused']:
                if not crawler_status['is_running']:
                    return
                time.sleep(1)

            if not crawler_status['is_running']:
                add_crawler_log("任务已停止")
                return

            crawler_status['current_keyword'] = keyword
            crawler_status['processed_keywords'] = i
            crawler_status['progress'] = int((i / len(keywords)) * 50)

            add_crawler_log(f"开始处理关键词: {keyword}")

            for page in range(1, pages_per_keyword + 1):
                add_crawler_log(f"处理第{page}页...")

                videos = crawler.search(keyword, page)

                if videos:
                    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    for video in videos:
                        video['搜索关键词'] = keyword
                        video['操作时间'] = current_time

                    all_videos.extend(videos)
                    crawler_status['total_videos'] = len(all_videos)
                    crawler_status['videos'] = all_videos
                    add_crawler_log(f"第{page}页成功获取到 {len(videos)} 个视频")
                else:
                    add_crawler_log(f"第{page}页未获取到数据")

                delay = random.uniform(3, 5)
                add_crawler_log(f"等待 {delay:.1f} 秒...")
                time.sleep(delay)

            keyword_count = len([v for v in all_videos if v['搜索关键词'] == keyword])
            add_crawler_log(f"关键词 '{keyword}' 处理完成，共获取 {keyword_count} 个视频")

        # 第二阶段：补充详细信息
        if all_videos:
            crawler_status['progress'] = 50
            crawler_status['current_task'] = '正在补充视频详细信息...'
            add_crawler_log("开始补充视频详细信息...")

            # 去重
            if remove_duplicates:
                df_temp = pd.DataFrame(all_videos)
                before_count = len(df_temp)
                df_temp = df_temp.drop_duplicates(subset=['bvid'], keep='first')
                after_count = len(df_temp)

                if before_count != after_count:
                    add_crawler_log(f"去除了 {before_count - after_count} 个重复视频")

                all_videos = df_temp.to_dict('records')

            # 补充详细信息
            if enable_detailed_info:
                enriched_videos = crawler.enrich_videos(all_videos,
                                                        progress_callback=lambda msg: add_crawler_log(msg))
            else:
                enriched_videos = all_videos

            # 第三阶段：保存数据
            crawler_status['progress'] = 90
            crawler_status['current_task'] = '正在保存数据...'
            add_crawler_log("开始保存数据...")

            df = pd.DataFrame(enriched_videos)

            columns_order = [
                'bvid', 'title', 'arcurl', 'description', 'author',
                'uploadDate', 'play', 'review', 'tag', 'pubdate',
                'duration', '搜索关键词', '操作时间'
            ]

            for col in columns_order:
                if col not in df.columns:
                    df[col] = ''

            df = df[columns_order]

            output_filename = os.path.join(DOWNLOAD_DIR, 'BVID.xlsx')
            df.to_excel(output_filename, index=False)

            crawler_status['progress'] = 100
            crawler_status['current_task'] = '任务完成！'
            crawler_status['videos'] = enriched_videos
            add_crawler_log(f"数据已保存到 {output_filename}")
            add_crawler_log(f"总共获取到 {len(df)} 个唯一视频数据")
        else:
            crawler_status['error'] = "未获取到任何数据"
            add_crawler_log("未获取到任何数据", True)

    except Exception as e:
        crawler_status['error'] = f"任务执行出错: {str(e)}"
        add_crawler_log(f"任务执行出错: {str(e)}", True)
    finally:
        crawler_status['is_running'] = False
        crawler_status['is_paused'] = False

        # 清理临时文件
        if filename.startswith('temp_keywords_'):
            try:
                temp_filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                if os.path.exists(temp_filepath):
                    os.remove(temp_filepath)
            except:
                pass


# ========== 下载任务 ==========
def run_yt_dlp(bvid, download_type, task_id):
    """运行yt-dlp下载"""
    url = f"https://www.bilibili.com/video/{bvid}"
    output_dir = os.path.join(DOWNLOAD_DIR, bvid)
    os.makedirs(output_dir, exist_ok=True)

    download_task_status[task_id] = {"status": "downloading", "progress": 0, "message": "开始下载..."}

    try:
        import shutil
        ffmpeg_path = shutil.which('ffmpeg')
        ffmpeg_dir = os.path.dirname(ffmpeg_path) if ffmpeg_path else '/opt/homebrew/bin'

        base_cmd = [
            "yt-dlp",
            "--no-warnings",
            "--newline",
            "--ffmpeg-location", ffmpeg_dir,
        ]

        if download_type == "audio":
            cmd = base_cmd + [
                "-f", "bestaudio[ext=m4a]/bestaudio",
                "-o", os.path.join(output_dir, "%(title)s.%(ext)s"),
                url
            ]
        elif download_type == "video_only":
            cmd = base_cmd + [
                "-f", "bestvideo[ext=mp4]/bestvideo",
                "-o", os.path.join(output_dir, "%(title)s_video.%(ext)s"),
                url
            ]
        elif download_type == "danmaku":
            cmd = base_cmd + [
                "--write-subs", "--sub-langs", "danmaku",
                "--skip-download",
                "-o", os.path.join(output_dir, "%(title)s"),
                url
            ]
        else:  # merged
            cmd = base_cmd + [
                "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
                "--merge-output-format", "mp4",
                "-o", os.path.join(output_dir, "%(title)s.%(ext)s"),
                url
            ]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=os.environ
        )

        output_lines = []
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            output_lines.append(line)
            download_task_status[task_id]["message"] = line[:100]

            if '%' in line:
                try:
                    match = re.search(r'(\d+\.?\d*)%', line)
                    if match:
                        progress = float(match.group(1))
                        download_task_status[task_id]["progress"] = min(progress, 99)
                except:
                    pass

        process.wait()

        if process.returncode == 0:
            files = os.listdir(output_dir) if os.path.exists(output_dir) else []
            if files:
                download_task_status[task_id] = {
                    "status": "completed",
                    "progress": 100,
                    "message": f"下载完成，共 {len(files)} 个文件",
                    "output_dir": output_dir
                }
            else:
                download_task_status[task_id] = {
                    "status": "error",
                    "message": "下载完成但未找到文件"
                }
        else:
            error_lines = [l for l in output_lines if 'error' in l.lower()]
            error_msg = error_lines[-1] if error_lines else output_lines[-1] if output_lines else "未知错误"
            download_task_status[task_id] = {
                "status": "error",
                "message": error_msg[:200]
            }

    except FileNotFoundError:
        download_task_status[task_id] = {
            "status": "error",
            "message": "yt-dlp 未安装，请运行: pip install yt-dlp"
        }
    except Exception as e:
        download_task_status[task_id] = {
            "status": "error",
            "message": f"异常: {str(e)}"
        }


# ========== 转写任务 ==========
def run_transcribe(bvid, audio_file, task_id, output_formats):
    """后台运行转写任务"""
    try:
        output_dir = os.path.join(DOWNLOAD_DIR, bvid)

        def progress_callback(message, progress):
            transcribe_status[task_id] = {
                "status": "transcribing",
                "progress": progress,
                "message": message
            }

        transcriber = get_transcriber(model_size="medium")
        transcriber.set_progress_callback(progress_callback)

        output = transcriber.transcribe_and_save(
            audio_file,
            output_dir,
            formats=output_formats,
            language="zh"
        )

        result: TranscriptResult = output["result"]

        transcribe_status[task_id] = {
            "status": "completed",
            "progress": 100,
            "message": "转写完成",
            "text": result.text,
            "timestamped_text": result.to_timestamped_text(),
            "segments": [
                {
                    "start": seg.start,
                    "end": seg.end,
                    "start_formatted": seg.start_formatted,
                    "end_formatted": seg.end_formatted,
                    "text": seg.text
                }
                for seg in result.segments
            ],
            "duration": result.duration,
            "language": result.language,
            "files": output["files"]
        }

    except Exception as e:
        transcribe_status[task_id] = {
            "status": "error",
            "progress": 0,
            "message": f"转写失败: {str(e)}"
        }


# ========== 前端路由 ==========
@app.route('/')
def index():
    return send_from_directory(FRONTEND_DIR, 'index.html')


@app.route('/<path:filename>')
def serve_static(filename):
    return send_from_directory(FRONTEND_DIR, filename)


# ========== 爬虫 API ==========
@app.route('/api/crawler/upload', methods=['POST'])
def crawler_upload_file():
    """上传关键词文件并开始爬取"""
    if 'file' not in request.files:
        return jsonify({'error': '没有文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '没有选择文件'}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        pages = request.form.get('pages', 5, type=int)
        enable_detailed_info = request.form.get('enable_detailed_info', 'true') == 'true'
        remove_duplicates = request.form.get('remove_duplicates', 'true') == 'true'

        thread = threading.Thread(
            target=run_crawler_task,
            args=(filename, pages, enable_detailed_info, remove_duplicates)
        )
        thread.daemon = True
        thread.start()

        keywords = read_keywords(filepath)

        return jsonify({
            'message': '文件上传成功，开始爬取数据',
            'filename': filename,
            'keywords_count': len(keywords),
            'keywords': keywords
        })

    return jsonify({'error': '文件类型不支持'}), 400


@app.route('/api/crawler/start-with-keywords', methods=['POST'])
def crawler_start_with_keywords():
    """使用手动输入的关键词开始爬取"""
    try:
        keywords_json = request.form.get('keywords')
        if not keywords_json:
            return jsonify({'error': '没有提供关键词'}), 400

        keywords = json.loads(keywords_json)
        if not keywords or not isinstance(keywords, list):
            return jsonify({'error': '关键词格式不正确'}), 400

        pages = request.form.get('pages', 5, type=int)
        enable_detailed_info = request.form.get('enable_detailed_info', 'true') == 'true'
        remove_duplicates = request.form.get('remove_duplicates', 'true') == 'true'

        temp_filename = f"temp_keywords_{int(time.time())}.xlsx"
        temp_filepath = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)

        df = pd.DataFrame({'item': keywords})
        df.to_excel(temp_filepath, index=False)

        thread = threading.Thread(
            target=run_crawler_task,
            args=(temp_filename, pages, enable_detailed_info, remove_duplicates)
        )
        thread.daemon = True
        thread.start()

        return jsonify({
            'message': '开始爬取数据',
            'keywords_count': len(keywords),
            'keywords': keywords
        })

    except Exception as e:
        return jsonify({'error': f'处理关键词失败: {str(e)}'}), 500


@app.route('/api/crawler/status')
def crawler_get_status():
    """获取爬虫状态"""
    return jsonify(crawler_status)


@app.route('/api/crawler/pause', methods=['POST'])
def crawler_pause():
    """暂停爬虫"""
    global crawler_status
    if crawler_status['is_running'] and not crawler_status['is_paused']:
        crawler_status['is_paused'] = True
        add_crawler_log("任务已暂停")
    return jsonify({'message': '任务已暂停'})


@app.route('/api/crawler/resume', methods=['POST'])
def crawler_resume():
    """继续爬虫"""
    global crawler_status
    if crawler_status['is_running'] and crawler_status['is_paused']:
        crawler_status['is_paused'] = False
        add_crawler_log("任务继续执行")
    return jsonify({'message': '任务继续执行'})


@app.route('/api/crawler/stop', methods=['POST'])
def crawler_stop():
    """停止爬虫"""
    global crawler_status
    crawler_status['is_running'] = False
    crawler_status['is_paused'] = False
    crawler_status['current_task'] = '任务已停止'
    add_crawler_log("任务已停止")
    return jsonify({'message': '任务已停止'})


@app.route('/api/crawler/download')
def crawler_download():
    """下载爬取结果"""
    filepath = os.path.join(DOWNLOAD_DIR, 'BVID.xlsx')
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True, download_name='BVID.xlsx')
    else:
        return jsonify({'error': '文件不存在'}), 404


# ========== 下载 API ==========
@app.route('/api/download', methods=['POST'])
def download_video():
    """启动下载任务"""
    data = request.json
    bvids = data.get('bvids', [])
    download_type = data.get('type', 'merged')

    task_ids = []
    for bvid in bvids:
        bvid = bvid.strip()
        if not bvid:
            continue
        if 'bilibili.com' in bvid:
            match = re.search(r'(BV[\w]+)', bvid)
            if match:
                bvid = match.group(1)

        task_id = f"{bvid}_{download_type}"
        task_ids.append(task_id)

        thread = threading.Thread(target=run_yt_dlp, args=(bvid, download_type, task_id))
        thread.daemon = True
        thread.start()

    return jsonify({"task_ids": task_ids})


@app.route('/api/download/status/<task_id>', methods=['GET'])
def get_download_status(task_id):
    """获取下载任务状态"""
    status = download_task_status.get(task_id, {"status": "unknown", "message": "任务不存在"})
    return jsonify(status)


@app.route('/api/downloads', methods=['GET'])
def list_downloads():
    """列出所有已下载的内容"""
    downloads = []

    if not os.path.exists(DOWNLOAD_DIR):
        return jsonify({"downloads": []})

    for bvid in os.listdir(DOWNLOAD_DIR):
        bvid_dir = os.path.join(DOWNLOAD_DIR, bvid)
        if not os.path.isdir(bvid_dir):
            continue

        files = []
        has_audio = False
        has_video = False
        has_transcript = False
        title = bvid

        for f in os.listdir(bvid_dir):
            filepath = os.path.join(bvid_dir, f)
            if not os.path.isfile(filepath):
                continue

            file_info = {
                "name": f,
                "size": os.path.getsize(filepath),
                "path": filepath
            }
            files.append(file_info)

            ext = os.path.splitext(f)[1].lower()
            if ext in ['.m4a', '.mp3', '.wav', '.aac']:
                has_audio = True
                title = os.path.splitext(f)[0]
            elif ext in ['.mp4', '.webm', '.flv', '.mkv']:
                has_video = True
                if not has_audio:
                    title = os.path.splitext(f)[0].replace('_video', '')
            elif ext == '.txt' and not f.endswith('_timestamped.txt'):
                has_transcript = True
            elif ext in ['.srt', '.json']:
                has_transcript = True

        if files:
            downloads.append({
                "bvid": bvid,
                "title": title,
                "files": files,
                "has_audio": has_audio,
                "has_video": has_video,
                "has_transcript": has_transcript,
                "file_count": len(files),
                "total_size": sum(f["size"] for f in files)
            })

    downloads.sort(key=lambda x: max(
        os.path.getmtime(f["path"]) for f in x["files"]
    ) if x["files"] else 0, reverse=True)

    return jsonify({"downloads": downloads})


@app.route('/api/files/<bvid>', methods=['GET'])
def list_files(bvid):
    """列出下载的文件"""
    output_dir = os.path.join(DOWNLOAD_DIR, bvid)
    if not os.path.exists(output_dir):
        return jsonify({"files": []})

    files = []
    for f in os.listdir(output_dir):
        filepath = os.path.join(output_dir, f)
        if os.path.isfile(filepath):
            files.append({
                "name": f,
                "size": os.path.getsize(filepath),
                "path": filepath
            })

    return jsonify({"files": files})


@app.route('/api/delete/<bvid>', methods=['DELETE'])
def delete_download(bvid):
    """删除下载的内容"""
    import shutil
    output_dir = os.path.join(DOWNLOAD_DIR, bvid)

    if not os.path.exists(output_dir):
        return jsonify({"error": "目录不存在"}), 404

    try:
        shutil.rmtree(output_dir)
        return jsonify({"success": True, "message": f"已删除 {bvid}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ========== 转写 API ==========
@app.route('/api/transcribe', methods=['POST'])
def transcribe_audio():
    """启动音频转文本任务"""
    data = request.json
    bvid = data.get('bvid')
    output_formats = data.get('formats', ['txt'])

    output_dir = os.path.join(DOWNLOAD_DIR, bvid)

    if not os.path.exists(output_dir):
        return jsonify({"error": f"目录不存在: {bvid}，请先下载视频"}), 404

    audio_file = None
    for f in os.listdir(output_dir):
        if f.endswith(('.m4a', '.mp3', '.wav', '.mp4', '.webm', '.flv', '.aac')):
            audio_file = os.path.join(output_dir, f)
            break

    if not audio_file:
        return jsonify({"error": "未找到音频/视频文件"}), 404

    task_id = f"transcribe_{bvid}"

    if task_id in transcribe_status and transcribe_status[task_id]["status"] == "completed":
        return jsonify({
            "task_id": task_id,
            "status": "completed",
            "cached": True,
            **transcribe_status[task_id]
        })

    transcribe_status[task_id] = {
        "status": "starting",
        "progress": 0,
        "message": "正在启动转写任务..."
    }

    thread = threading.Thread(
        target=run_transcribe,
        args=(bvid, audio_file, task_id, output_formats)
    )
    thread.daemon = True
    thread.start()

    return jsonify({"task_id": task_id, "status": "started"})

@app.route('/api/transcript/<bvid>', methods=['GET'])
def get_transcript_content(bvid):
    """获取转写文本内容"""
    output_dir = os.path.join(DOWNLOAD_DIR, bvid)

    if not os.path.exists(output_dir):
        return jsonify({"error": "目录不存在"}), 404

    # 查找txt文件
    transcript_text = None
    timestamped_text = None

    for f in os.listdir(output_dir):
        filepath = os.path.join(output_dir, f)
        if f.endswith('.txt') and not f.endswith('_timestamped.txt'):
            try:
                with open(filepath, 'r', encoding='utf-8') as file:
                    transcript_text = file.read()
            except:
                pass
        elif f.endswith('_timestamped.txt'):
            try:
                with open(filepath, 'r', encoding='utf-8') as file:
                    timestamped_text = file.read()
            except:
                pass

    if transcript_text is None:
        return jsonify({"error": "未找到转写文件"}), 404

    return jsonify({
        "text": transcript_text,
        "timestamped_text": timestamped_text
    })

@app.route('/api/transcribe/status/<task_id>', methods=['GET'])
def get_transcribe_status(task_id):
    """获取转写任务状态"""
    status = transcribe_status.get(task_id, {"status": "unknown", "message": "任务不存在"})
    return jsonify(status)


# ========== AI总结 API ==========
@app.route('/api/summarize', methods=['POST'])
def summarize_text():
    """调用API总结文本"""
    data = request.json
    text = data.get('text', '')
    base_url = data.get('base_url', 'https://api.openai.com/v1')
    api_key = data.get('api_key', '')
    prompt = data.get('prompt', '请总结以下内容的主要观点：')
    model = data.get('model', 'gpt-3.5-turbo')

    if not api_key:
        return jsonify({"error": "请提供API Key"}), 400

    if not text:
        return jsonify({"error": "请提供要总结的文本"}), 400

    try:
        client = OpenAI(base_url=base_url, api_key=api_key)

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是一个专业的内容总结助手。"},
                {"role": "user", "content": f"{prompt}\n\n{text}"}
            ]
        )

        summary = response.choices[0].message.content
        return jsonify({"summary": summary})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ========== 启动服务器 ==========
if __name__ == '__main__':
    print(f"\n{'=' * 60}")
    print("🎬 B站视频信息爬取、下载与AI总结工具")
    print(f"{'=' * 60}")
    print(f"📁 Frontend: {FRONTEND_DIR}")
    print(f"📁 Downloads: {DOWNLOAD_DIR}")
    print(f"📁 Uploads: {UPLOAD_DIR}")
    print(f"\n🌐 请在浏览器中打开: http://localhost:5000")
    print(f"{'=' * 60}\n")

    app.run(debug=True, port=5000, threaded=True)


