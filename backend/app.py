# app.py
import os
import subprocess
import json
import threading
import traceback
import re
import time
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
from openai import OpenAI

# 导入转写模块
from transcriber import get_transcriber, TranscriptResult

# 确保 PATH 包含 homebrew 的 bin 目录
os.environ['PATH'] = '/opt/homebrew/bin:/usr/local/bin:' + os.environ.get('PATH', '')

# 获取项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, 'frontend')
DOWNLOAD_DIR = os.path.join(BASE_DIR, 'downloads')

# 创建 Flask 应用，指定静态文件目录
app = Flask(__name__,
            static_folder=FRONTEND_DIR,
            static_url_path='')
CORS(app, supports_credentials=True, resources={r"/*": {"origins": "*"}})

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# 全局状态存储
task_status = {}
transcribe_status = {}


def check_dependencies():
    """检查依赖"""
    import shutil
    yt_dlp = shutil.which('yt-dlp')
    ffmpeg = shutil.which('ffmpeg')
    ffprobe = shutil.which('ffprobe')

    print(f"[CHECK] yt-dlp: {yt_dlp}")
    print(f"[CHECK] ffmpeg: {ffmpeg}")
    print(f"[CHECK] ffprobe: {ffprobe}")

    return yt_dlp, ffmpeg


def run_yt_dlp(bvid, download_type, task_id):
    """运行yt-dlp下载"""
    url = f"https://www.bilibili.com/video/{bvid}"
    output_dir = os.path.join(DOWNLOAD_DIR, bvid)
    os.makedirs(output_dir, exist_ok=True)

    task_status[task_id] = {"status": "downloading", "progress": 0, "message": "开始下载..."}

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

        print(f"\n{'=' * 50}")
        print(f"[TASK] {task_id}")
        print(f"[CMD] {' '.join(cmd)}")
        print(f"{'=' * 50}")

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
            print(f"[yt-dlp] {line}")

            task_status[task_id]["message"] = line[:100]

            if '%' in line:
                try:
                    match = re.search(r'(\d+\.?\d*)%', line)
                    if match:
                        progress = float(match.group(1))
                        task_status[task_id]["progress"] = min(progress, 99)
                except:
                    pass

        process.wait()

        print(f"[RESULT] Return code: {process.returncode}")

        if process.returncode == 0:
            files = os.listdir(output_dir) if os.path.exists(output_dir) else []
            if files:
                task_status[task_id] = {
                    "status": "completed",
                    "progress": 100,
                    "message": f"下载完成，共 {len(files)} 个文件",
                    "output_dir": output_dir
                }
            else:
                task_status[task_id] = {
                    "status": "error",
                    "message": "下载完成但未找到文件"
                }
        else:
            error_lines = [l for l in output_lines if 'error' in l.lower() or 'ERROR' in l]
            error_msg = error_lines[-1] if error_lines else output_lines[-1] if output_lines else "未知错误"
            task_status[task_id] = {
                "status": "error",
                "message": error_msg[:200]
            }

    except FileNotFoundError:
        task_status[task_id] = {
            "status": "error",
            "message": "yt-dlp 未安装，请运行: brew install yt-dlp"
        }
    except Exception as e:
        print(f"[EXCEPTION] {traceback.format_exc()}")
        task_status[task_id] = {
            "status": "error",
            "message": f"异常: {str(e)}"
        }


def run_transcribe(bvid, audio_file, task_id, output_formats):
    """后台运行转写任务"""
    try:
        output_dir = os.path.join(DOWNLOAD_DIR, bvid)

        # 创建进度回调
        def progress_callback(message, progress):
            transcribe_status[task_id] = {
                "status": "transcribing",
                "progress": progress,
                "message": message
            }

        # 获取转写器并设置回调
        transcriber = get_transcriber(model_size="medium")
        transcriber.set_progress_callback(progress_callback)

        # 执行转写并保存多种格式
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
        print(f"[Transcribe Error] {traceback.format_exc()}")
        transcribe_status[task_id] = {
            "status": "error",
            "progress": 0,
            "message": f"转写失败: {str(e)}"
        }


# ========== 前端路由 ==========
@app.route('/')
def index():
    """首页"""
    return send_from_directory(FRONTEND_DIR, 'index.html')


@app.route('/<path:filename>')
def serve_static(filename):
    """提供静态文件"""
    return send_from_directory(FRONTEND_DIR, filename)


# ========== API 路由 ==========
@app.route('/api/download', methods=['POST'])
def download_video():
    """启动下载任务"""
    data = request.json
    bvids = data.get('bvids', [])
    download_type = data.get('type', 'merged')

    print(f"\n[API] Download request: bvids={bvids}, type={download_type}")

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


@app.route('/api/status/<task_id>', methods=['GET'])
def get_status(task_id):
    """获取下载任务状态"""
    status = task_status.get(task_id, {"status": "unknown", "message": "任务不存在"})
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

        # 收集该BV号下的所有文件
        files = []
        has_audio = False
        has_video = False
        has_transcript = False
        title = bvid  # 默认标题为BV号

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

            # 判断文件类型
            ext = os.path.splitext(f)[1].lower()
            if ext in ['.m4a', '.mp3', '.wav', '.aac']:
                has_audio = True
                # 从音频文件名提取标题
                title = os.path.splitext(f)[0]
            elif ext in ['.mp4', '.webm', '.flv', '.mkv']:
                has_video = True
                if not has_audio:  # 音频文件名优先
                    title = os.path.splitext(f)[0].replace('_video', '')
            elif ext == '.txt' and not f.endswith('_timestamped.txt'):
                has_transcript = True
            elif ext in ['.srt', '.json']:
                has_transcript = True

        if files:  # 只添加非空目录
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

    # 按修改时间排序（最新的在前）
    downloads.sort(key=lambda x: max(
        os.path.getmtime(f["path"]) for f in x["files"]
    ) if x["files"] else 0, reverse=True)

    return jsonify({"downloads": downloads})


@app.route('/api/transcribe', methods=['POST'])
def transcribe_audio():
    """启动音频转文本任务"""
    data = request.json
    bvid = data.get('bvid')
    # 支持选择输出格式，默认只输出纯文本
    output_formats = data.get('formats', ['txt'])

    output_dir = os.path.join(DOWNLOAD_DIR, bvid)

    if not os.path.exists(output_dir):
        return jsonify({"error": f"目录不存在: {bvid}，请先下载视频"}), 404

    # 查找音频/视频文件
    audio_file = None
    for f in os.listdir(output_dir):
        if f.endswith(('.m4a', '.mp3', '.wav', '.mp4', '.webm', '.flv', '.aac')):
            audio_file = os.path.join(output_dir, f)
            break

    if not audio_file:
        files = os.listdir(output_dir)
        return jsonify({"error": f"未找到音频/视频文件。目录中的文件: {files}"}), 404

    task_id = f"transcribe_{bvid}"

    # 检查是否已有完成的转写
    if task_id in transcribe_status and transcribe_status[task_id]["status"] == "completed":
        return jsonify({
            "task_id": task_id,
            "status": "completed",
            "cached": True,
            **transcribe_status[task_id]
        })

    # 启动后台转写任务
    print(f"[Transcribe] Starting task: {task_id}")
    print(f"[Transcribe] Audio file: {audio_file}")
    print(f"[Transcribe] Output formats: {output_formats}")

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


@app.route('/api/transcribe/status/<task_id>', methods=['GET'])
def get_transcribe_status(task_id):
    """获取转写任务状态"""
    status = transcribe_status.get(task_id, {"status": "unknown", "message": "任务不存在"})
    return jsonify(status)


@app.route('/api/summarize', methods=['POST'])
def summarize_text():
    """调用API总结文本"""
    data = request.json
    text = data.get('text', '')
    include_timestamps = data.get('include_timestamps', False)
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
        print(f"[Summarize Error] {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


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


@app.route('/api/download-file', methods=['GET'])
def download_file():
    """下载文件"""
    filepath = request.args.get('path')
    if filepath and os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    return jsonify({"error": "文件不存在"}), 404


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


if __name__ == '__main__':
    print(f"\n{'=' * 50}")
    print("B站视频下载与AI总结工具")
    print(f"{'=' * 50}")

    check_dependencies()

    print(f"\nFrontend: {FRONTEND_DIR}")
    print(f"Downloads: {DOWNLOAD_DIR}")
    print(f"\n🌐 请在浏览器中打开: http://localhost:5000")
    print(f"{'=' * 50}\n")

    app.run(debug=True, port=5000, threaded=True)

