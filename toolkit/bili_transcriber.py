"""
音频转写封装（基于 OpenAI Whisper）

核心经验：
1. 首次加载模型会自动下载到 ~/.cache/whisper/，medium 约 769MB
2. 中文建议设置 initial_prompt 强制简体中文输出
3. 支持多种输出格式：txt, timestamped, srt, vtt, json
4. 模型选择：medium 是质量/速度的平衡点
"""

import os
import json
import whisper
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Callable


@dataclass
class TranscriptSegment:
    """转写片段"""
    start: float      # 开始时间（秒）
    end: float        # 结束时间（秒）
    text: str         # 文本内容

    @property
    def start_formatted(self) -> str:
        """格式化开始时间 HH:MM:SS.mmm"""
        return self._format_time(self.start)

    @property
    def end_formatted(self) -> str:
        """格式化结束时间 HH:MM:SS.mmm"""
        return self._format_time(self.end)

    @property
    def duration(self) -> float:
        """片段时长（秒）"""
        return self.end - self.start

    @staticmethod
    def _format_time(seconds: float) -> str:
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
    text: str                 # 完整文本
    segments: List[TranscriptSegment]  # 分段列表
    language: str             # 检测到的语言
    duration: float           # 音频总时长（秒）

    def to_plain_text(self) -> str:
        return self.text

    def to_timestamped_text(self) -> str:
        lines = []
        for seg in self.segments:
            lines.append(f"[{seg.start_formatted} → {seg.end_formatted}] {seg.text}")
        return "\n".join(lines)

    def merged(self, min_duration: float = 3.0) -> "TranscriptResult":
        """合并 < min_duration 秒的小段,看着不那么碎(借鉴 v1)。

        相邻短段会被前一段吸收;末尾仍可能有一个短段。
        返回新对象,不修改原对象。
        """
        if not self.segments:
            return self
        merged: List[TranscriptSegment] = []
        current = TranscriptSegment(
            start=self.segments[0].start,
            end=self.segments[0].end,
            text=self.segments[0].text,
        )
        for seg in self.segments[1:]:
            if (current.end - current.start) < min_duration:
                current = TranscriptSegment(
                    start=current.start,
                    end=seg.end,
                    text=(current.text + " " + seg.text).strip(),
                )
            else:
                merged.append(current)
                current = TranscriptSegment(start=seg.start, end=seg.end, text=seg.text)
        merged.append(current)
        return TranscriptResult(
            text=self.text,
            segments=merged,
            language=self.language,
            duration=self.duration,
        )

    def to_payload(self) -> Dict:
        """打成给 store.update_video_transcription 用的 dict。"""
        return {
            "transcription": self.text,
            "timestamped_text": self.to_timestamped_text(),
            "segments": [
                {
                    "start": seg.start,
                    "end": seg.end,
                    "start_formatted": seg.start_formatted,
                    "end_formatted": seg.end_formatted,
                    "text": seg.text,
                }
                for seg in self.segments
            ],
            "language": self.language,
            "audio_duration": self.duration,
        }

    def to_srt(self) -> str:
        lines = []
        for i, seg in enumerate(self.segments, 1):
            start_srt = self._to_srt_time(seg.start)
            end_srt = self._to_srt_time(seg.end)
            lines.extend([str(i), f"{start_srt} --> {end_srt}", seg.text.strip(), ""])
        return "\n".join(lines)

    def to_vtt(self) -> str:
        lines = ["WEBVTT", ""]
        for seg in self.segments:
            start_vtt = self._to_vtt_time(seg.start)
            end_vtt = self._to_vtt_time(seg.end)
            lines.extend([f"{start_vtt} --> {end_vtt}", seg.text.strip(), ""])
        return "\n".join(lines)

    def to_json(self) -> str:
        data = {
            "text": self.text,
            "language": self.language,
            "duration": self.duration,
            "segments": [asdict(seg) for seg in self.segments],
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    @staticmethod
    def _to_srt_time(seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"

    @staticmethod
    def _to_vtt_time(seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"


class WhisperTranscriber:
    """Whisper 转写器封装"""

    MODEL_SIZES = {
        "tiny":   {"size": "39M",   "memory": "~1GB",  "speed": "~32x"},
        "base":   {"size": "74M",   "memory": "~1GB",  "speed": "~16x"},
        "small":  {"size": "244M",  "memory": "~2GB",  "speed": "~6x"},
        "medium": {"size": "769M",  "memory": "~5GB",  "speed": "~2x"},
        "large":  {"size": "1550M", "memory": "~10GB", "speed": "~1x"},
    }

    SIMPLIFIED_CHINESE_PROMPT = "以下是普通话的句子，请使用简体中文输出。"

    def __init__(self, model_size: str = "medium"):
        self.model_size = model_size
        self.model = None
        self._progress_callback: Optional[Callable[[str, float], None]] = None

    def load_model(self):
        """懒加载 Whisper 模型"""
        if self.model is None:
            print(f"[Whisper] 加载模型: {self.model_size} ...")
            self.model = whisper.load_model(self.model_size)
            print(f"[Whisper] 模型加载完成")
        return self.model

    def set_progress_callback(self, callback: Callable[[str, float], None]):
        self._progress_callback = callback

    def _report(self, message: str, progress: float):
        if self._progress_callback:
            self._progress_callback(message, progress)
        print(f"[Whisper] {message} ({progress:.0f}%)")

    def get_audio_duration(self, audio_path: str) -> float:
        audio = whisper.load_audio(audio_path)
        return len(audio) / 16000  # Whisper 采样率 16kHz

    def transcribe(
        self,
        audio_path: str,
        language: str = "zh",
        task: str = "transcribe",
        use_simplified_chinese: bool = True,
        **kwargs
    ) -> TranscriptResult:
        """
        转写音频文件

        Args:
            audio_path: 音频/视频文件路径
            language: 语言代码，zh=中文，None=自动检测
            task: "transcribe" 保留原语言，"translate" 翻译成英文
            use_simplified_chinese: 是否强制简体中文
            **kwargs: 其他 whisper 参数
        """
        self._report("加载模型", 0)
        model = self.load_model()

        self._report("分析音频", 10)
        duration = self.get_audio_duration(audio_path)
        self._report(f"音频时长: {duration/60:.1f} 分钟", 15)

        self._report("转写中", 20)

        transcribe_kwargs = {
            "language": language,
            "task": task,
            "verbose": False,
            **kwargs,
        }

        if use_simplified_chinese and language in ("zh", "Chinese", None):
            transcribe_kwargs["initial_prompt"] = self.SIMPLIFIED_CHINESE_PROMPT

        result = model.transcribe(audio_path, **transcribe_kwargs)

        self._report("处理结果", 90)

        segments = [
            TranscriptSegment(start=seg["start"], end=seg["end"], text=seg["text"].strip())
            for seg in result.get("segments", [])
        ]

        self._report("转写完成", 100)

        return TranscriptResult(
            text=result["text"],
            segments=segments,
            language=result.get("language", language or "unknown"),
            duration=duration,
        )

    def transcribe_and_save(
        self,
        audio_path: str,
        output_dir: str,
        formats: List[str] = None,
        **kwargs
    ) -> Dict:
        """
        转写并保存多种格式

        Args:
            audio_path: 音频/视频文件路径
            output_dir: 输出目录
            formats: 输出格式列表 ["txt", "timestamped", "srt", "vtt", "json"]
            **kwargs: 传递给 transcribe() 的参数

        Returns:
            {"result": TranscriptResult, "files": {"txt": path, ...}}
        """
        if formats is None:
            formats = ["txt", "timestamped", "srt"]

        result = self.transcribe(audio_path, **kwargs)

        os.makedirs(output_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(audio_path))[0]
        saved = {}

        if "txt" in formats:
            path = os.path.join(output_dir, f"{base_name}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(result.to_plain_text())
            saved["txt"] = path

        if "timestamped" in formats:
            path = os.path.join(output_dir, f"{base_name}_timestamped.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(result.to_timestamped_text())
            saved["timestamped"] = path

        if "srt" in formats:
            path = os.path.join(output_dir, f"{base_name}.srt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(result.to_srt())
            saved["srt"] = path

        if "vtt" in formats:
            path = os.path.join(output_dir, f"{base_name}.vtt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(result.to_vtt())
            saved["vtt"] = path

        if "json" in formats:
            path = os.path.join(output_dir, f"{base_name}.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write(result.to_json())
            saved["json"] = path

        return {"result": result, "files": saved}


# ========== 命令行测试 ==========
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python bili_transcriber.py <audio_file> [output_dir]")
        print("Example: python bili_transcriber.py ./downloads/BV1xxxxx/video.mp4")
        sys.exit(1)

    audio_file = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(audio_file)

    if not os.path.exists(audio_file):
        print(f"错误: 文件不存在: {audio_file}")
        sys.exit(1)

    transcriber = WhisperTranscriber(model_size="medium")

    def progress_cb(msg, pct):
        print(f"[{pct:5.1f}%] {msg}")

    transcriber.set_progress_callback(progress_cb)

    output = transcriber.transcribe_and_save(
        audio_file,
        out_dir,
        formats=["txt", "timestamped", "srt", "json"],
        language="zh",
        use_simplified_chinese=True,
    )

    print("\n输出文件:")
    for fmt, path in output["files"].items():
        print(f"  {fmt}: {path}")
