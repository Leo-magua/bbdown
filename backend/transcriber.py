# backend/transcriber.py
"""
音频转写模块
支持：分段输出、时间戳、多种输出格式
"""

import os
import json
import whisper
from dataclasses import dataclass, asdict
from typing import List, Optional, Callable


@dataclass
class TranscriptSegment:
    """转写片段"""
    start: float  # 开始时间（秒）
    end: float  # 结束时间（秒）
    text: str  # 文本内容

    @property
    def start_formatted(self) -> str:
        """格式化开始时间 HH:MM:SS"""
        return self._format_time(self.start)

    @property
    def end_formatted(self) -> str:
        """格式化结束时间 HH:MM:SS"""
        return self._format_time(self.end)

    @property
    def duration(self) -> float:
        """片段时长（秒）"""
        return self.end - self.start

    @staticmethod
    def _format_time(seconds: float) -> str:
        """将秒数转换为 HH:MM:SS 格式"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"
        return f"{minutes:02d}:{secs:02d}.{ms:03d}"


@dataclass
class TranscriptResult:
    """转写结果"""
    text: str  # 完整文本
    segments: List[TranscriptSegment]  # 分段列表
    language: str  # 检测到的语言
    duration: float  # 音频总时长（秒）

    def to_plain_text(self) -> str:
        """输出纯文本"""
        return self.text

    def to_timestamped_text(self) -> str:
        """输出带时间戳的文本"""
        lines = []
        for seg in self.segments:
            lines.append(f"[{seg.start_formatted} -> {seg.end_formatted}] {seg.text}")
        return "\n".join(lines)

    def to_srt(self) -> str:
        """输出 SRT 字幕格式"""
        lines = []
        for i, seg in enumerate(self.segments, 1):
            start_srt = self._to_srt_time(seg.start)
            end_srt = self._to_srt_time(seg.end)
            lines.append(f"{i}")
            lines.append(f"{start_srt} --> {end_srt}")
            lines.append(seg.text.strip())
            lines.append("")
        return "\n".join(lines)

    def to_vtt(self) -> str:
        """输出 VTT 字幕格式"""
        lines = ["WEBVTT", ""]
        for seg in self.segments:
            start_vtt = self._to_vtt_time(seg.start)
            end_vtt = self._to_vtt_time(seg.end)
            lines.append(f"{start_vtt} --> {end_vtt}")
            lines.append(seg.text.strip())
            lines.append("")
        return "\n".join(lines)

    def to_json(self) -> str:
        """输出 JSON 格式"""
        data = {
            "text": self.text,
            "language": self.language,
            "duration": self.duration,
            "segments": [asdict(seg) for seg in self.segments]
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    def get_segments_by_time(self, start: float, end: float) -> List[TranscriptSegment]:
        """获取指定时间范围内的片段"""
        return [seg for seg in self.segments if seg.start >= start and seg.end <= end]

    def merge_short_segments(self, min_duration: float = 3.0) -> 'TranscriptResult':
        """合并过短的片段"""
        if not self.segments:
            return self

        merged = []
        current = self.segments[0]

        for seg in self.segments[1:]:
            if current.duration < min_duration:
                # 合并到下一个片段
                current = TranscriptSegment(
                    start=current.start,
                    end=seg.end,
                    text=current.text + " " + seg.text
                )
            else:
                merged.append(current)
                current = seg

        merged.append(current)

        return TranscriptResult(
            text=self.text,
            segments=merged,
            language=self.language,
            duration=self.duration
        )

    @staticmethod
    def _to_srt_time(seconds: float) -> str:
        """转换为 SRT 时间格式 HH:MM:SS,mmm"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"

    @staticmethod
    def _to_vtt_time(seconds: float) -> str:
        """转换为 VTT 时间格式 HH:MM:SS.mmm"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


class WhisperTranscriber:
    """Whisper 转写器"""

    # 模型大小及其内存需求
    MODEL_SIZES = {
        "tiny": {"size": "39M", "memory": "~1GB", "speed": "~32x"},
        "base": {"size": "74M", "memory": "~1GB", "speed": "~16x"},
        "small": {"size": "244M", "memory": "~2GB", "speed": "~6x"},
        "medium": {"size": "769M", "memory": "~5GB", "speed": "~2x"},
        "large": {"size": "1550M", "memory": "~10GB", "speed": "~1x"},
    }

    # 简体中文引导提示词
    SIMPLIFIED_CHINESE_PROMPT = "以下是普通话的句子，请使用简体中文输出。"

    def __init__(self, model_size: str = "medium"):
        """
        初始化转写器

        Args:
            model_size: 模型大小 (tiny, base, small, medium, large)
        """
        self.model_size = model_size
        self.model = None
        self._progress_callback: Optional[Callable] = None

    def load_model(self):
        """加载 Whisper 模型"""
        if self.model is None:
            print(f"[Transcriber] Loading Whisper model ({self.model_size})...")
            self.model = whisper.load_model(self.model_size)
            print(f"[Transcriber] Model loaded!")
        return self.model

    def set_progress_callback(self, callback: Callable[[str, float], None]):
        """
        设置进度回调函数

        Args:
            callback: 回调函数，接收 (message, progress_percent) 参数
        """
        self._progress_callback = callback

    def _report_progress(self, message: str, progress: float):
        """报告进度"""
        if self._progress_callback:
            self._progress_callback(message, progress)
        print(f"[Transcriber] {message} ({progress:.1f}%)")

    def get_audio_duration(self, audio_path: str) -> float:
        """获取音频时长（秒）"""
        audio = whisper.load_audio(audio_path)
        return len(audio) / 16000  # Whisper 采样率 16kHz

    def transcribe(
            self,
            audio_path: str,
            language: str = "zh",
            task: str = "transcribe",
            word_timestamps: bool = False,
            use_simplified_chinese: bool = True,
            **kwargs
    ) -> TranscriptResult:
        """
        转写音频文件

        Args:
            audio_path: 音频文件路径
            language: 语言代码 (zh, en, ja, etc.)，None 表示自动检测
            task: "transcribe" 保留原语言，"translate" 翻译成英文
            word_timestamps: 是否输出词级时间戳
            use_simplified_chinese: 是否强制使用简体中文（仅对中文有效）
            **kwargs: 其他 whisper 参数

        Returns:
            TranscriptResult 对象
        """
        self._report_progress("正在加载模型...", 0)
        model = self.load_model()

        self._report_progress("正在分析音频...", 10)
        duration = self.get_audio_duration(audio_path)
        self._report_progress(f"音频时长: {duration / 60:.1f} 分钟", 15)

        self._report_progress("正在转写...", 20)

        # 构建转写参数
        transcribe_kwargs = {
            "language": language,
            "task": task,
            "word_timestamps": word_timestamps,
            "verbose": False,
            **kwargs
        }

        # 如果是中文且需要简体中文，添加引导提示词
        if use_simplified_chinese and language in ["zh", "Chinese", None]:
            transcribe_kwargs["initial_prompt"] = self.SIMPLIFIED_CHINESE_PROMPT
            print(f"[Transcriber] 使用简体中文引导提示")

        # 执行转写
        result = model.transcribe(audio_path, **transcribe_kwargs)

        self._report_progress("正在处理结果...", 90)

        # 构建分段结果
        segments = []
        for seg in result.get("segments", []):
            segments.append(TranscriptSegment(
                start=seg["start"],
                end=seg["end"],
                text=seg["text"].strip()
            ))

        self._report_progress("转写完成!", 100)

        return TranscriptResult(
            text=result["text"],
            segments=segments,
            language=result.get("language", language),
            duration=duration
        )

    def transcribe_and_save(
            self,
            audio_path: str,
            output_dir: str,
            formats: List[str] = None,
            **kwargs
    ) -> dict:
        """
        转写并保存多种格式

        Args:
            audio_path: 音频文件路径
            output_dir: 输出目录
            formats: 输出格式列表 ["txt", "srt", "vtt", "json", "timestamped"]
            **kwargs: 传递给 transcribe() 的参数

        Returns:
            包含各格式文件路径的字典
        """
        if formats is None:
            formats = ["txt", "timestamped", "srt", "json"]

        result = self.transcribe(audio_path, **kwargs)

        os.makedirs(output_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(audio_path))[0]

        saved_files = {}

        if "txt" in formats:
            path = os.path.join(output_dir, f"{base_name}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(result.to_plain_text())
            saved_files["txt"] = path

        if "timestamped" in formats:
            path = os.path.join(output_dir, f"{base_name}_timestamped.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(result.to_timestamped_text())
            saved_files["timestamped"] = path

        if "srt" in formats:
            path = os.path.join(output_dir, f"{base_name}.srt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(result.to_srt())
            saved_files["srt"] = path

        if "vtt" in formats:
            path = os.path.join(output_dir, f"{base_name}.vtt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(result.to_vtt())
            saved_files["vtt"] = path

        if "json" in formats:
            path = os.path.join(output_dir, f"{base_name}.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write(result.to_json())
            saved_files["json"] = path

        return {
            "result": result,
            "files": saved_files
        }


# 全局转写器实例（懒加载）
_transcriber: Optional[WhisperTranscriber] = None


def get_transcriber(model_size: str = "medium") -> WhisperTranscriber:
    """获取全局转写器实例"""
    global _transcriber
    if _transcriber is None:
        _transcriber = WhisperTranscriber(model_size)
    return _transcriber


# ============ 命令行测试 ============
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python transcriber.py <audio_file> [output_dir]")
        print("\nSupported formats: mp3, m4a, wav, mp4, webm, flv")
        sys.exit(1)

    audio_file = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(audio_file)

    if not os.path.exists(audio_file):
        print(f"Error: File not found: {audio_file}")
        sys.exit(1)

    print(f"\n{'=' * 50}")
    print(f"Audio file: {audio_file}")
    print(f"Output dir: {output_dir}")
    print(f"{'=' * 50}\n")

    transcriber = WhisperTranscriber(model_size="medium")


    # 设置进度回调
    def progress_callback(message, progress):
        print(f"[{progress:5.1f}%] {message}")


    transcriber.set_progress_callback(progress_callback)

    # 转写并保存（使用简体中文）
    output = transcriber.transcribe_and_save(
        audio_file,
        output_dir,
        formats=["txt", "timestamped", "srt", "json"],
        language="zh",
        use_simplified_chinese=True  # 强制简体中文
    )

    print(f"\n{'=' * 50}")
    print("Saved files:")
    for fmt, path in output["files"].items():
        print(f"  {fmt}: {path}")
    print(f"{'=' * 50}")

    # 打印前5个片段
    print("\nFirst 5 segments:")
    for seg in output["result"].segments[:5]:
        print(f"  [{seg.start_formatted}] {seg.text[:50]}...")
