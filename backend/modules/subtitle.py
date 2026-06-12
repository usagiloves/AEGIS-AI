import os
import time
import asyncio
from pathlib import Path
from backend.config import WHISPER_MODEL_SIZE, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE, OUTPUT_DIR
from faster_whisper import WhisperModel

# Bộ đếm mô hình toàn cục để chia sẻ giữa các instance
_whisper_model = None
_unload_task = None

class SubtitleEngine:
    def __init__(self, progress_callback=None):
        self.progress_callback = progress_callback
        self.model = None
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = None

    def _log(self, message):
        if self.progress_callback:
            if asyncio.iscoroutinefunction(self.progress_callback):
                if self.loop and self.loop.is_running():
                    asyncio.run_coroutine_threadsafe(self.progress_callback({"status": "info", "message": message}), self.loop)
                else:
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            asyncio.run_coroutine_threadsafe(self.progress_callback({"status": "info", "message": message}), loop)
                        else:
                            loop.run_until_complete(self.progress_callback({"status": "info", "message": message}))
                    except RuntimeError:
                        asyncio.run(self.progress_callback({"status": "info", "message": message}))
            else:
                self.progress_callback({"status": "info", "message": message})
        else:
            print(f"[SubtitleEngine] {message}")

    def load_model(self):
        """
        Khởi tạo và tải mô hình Whisper vào bộ nhớ đệm (ưu tiên GPU).
        """
        global _whisper_model, _unload_task
        
        # Hủy task tự động unload đang chạy nếu có yêu cầu tải mới
        if _unload_task is not None:
            try:
                _unload_task.cancel()
            except Exception:
                pass
            _unload_task = None

        if _whisper_model is None:
            self._log(f"Đang tải mô hình Whisper '{WHISPER_MODEL_SIZE}' trên thiết bị '{WHISPER_DEVICE}'...")
            try:
                _whisper_model = WhisperModel(
                    WHISPER_MODEL_SIZE, 
                    device=WHISPER_DEVICE, 
                    compute_type=WHISPER_COMPUTE_TYPE
                )
                self._log("Tải mô hình Whisper thành công!")
            except Exception as e:
                self._log(f"Lỗi khi load GPU, tự động chuyển sang CPU: {str(e)}")
                _whisper_model = WhisperModel(
                    "small", 
                    device="cpu", 
                    compute_type="int8"
                )
                self._log("Tải mô hình Whisper (CPU mode) thành công!")
        
        self.model = _whisper_model

    def schedule_auto_unload(self):
        """
        Lên lịch tự động giải phóng Whisper khỏi bộ nhớ sau khi không hoạt động.
        """
        global _unload_task
        if _unload_task is not None:
            try:
                _unload_task.cancel()
            except Exception:
                pass
            _unload_task = None
            
        from backend.config import WHISPER_UNLOAD_TIMEOUT
        if WHISPER_UNLOAD_TIMEOUT > 0 and self.loop and self.loop.is_running():
            _unload_task = self.loop.create_task(self._unload_model_after_delay(WHISPER_UNLOAD_TIMEOUT))

    async def _unload_model_after_delay(self, delay):
        global _whisper_model, _unload_task
        try:
            await asyncio.sleep(delay)
            if _whisper_model is not None:
                self._log(f"Đã kích hoạt chế độ tiết kiệm năng lượng: Tự động giải phóng Whisper khỏi bộ nhớ GPU CUDA sau {delay} giây không hoạt động.")
                _whisper_model = None
                self.model = None
                
                # Giải phóng bộ nhớ CUDA
                import gc
                gc.collect()
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                        self._log("Đã dọn dẹp CUDA VRAM Cache thành công!")
                except ImportError:
                    pass
        except asyncio.CancelledError:
            pass
        finally:
            _unload_task = None

    def generate_srt_content(self, segments, offset=0.0):
        """
        Chuyển đổi kết quả Whisper segments thành cấu trúc nội dung file SRT, hỗ trợ thời gian lệch (offset).
        """
        srt_lines = []
        for i, segment in enumerate(segments, start=1):
            start_sec = max(0, segment.start + offset)
            end_sec = max(0, segment.end + offset)
            start = self.format_time(start_sec)
            end = self.format_time(end_sec)
            text = segment.text.strip()
            srt_lines.append(f"{i}\n{start} --> {end}\n{text}\n")
        return "\n".join(srt_lines)

    @staticmethod
    def format_time(seconds):
        """
        Định dạng thời gian theo chuẩn SRT: HH:MM:SS,mmm
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    async def transcribe(self, media_path, offset=0.0):
        """
        Trích xuất phụ đề gốc từ file video/audio.
        """
        self.load_model()
        self._log(f"Đang phân tích âm thanh từ file: {os.path.basename(media_path)}...")
        
        def run_transcribe():
            segments, info = self.model.transcribe(
                str(media_path), 
                beam_size=3, 
                word_timestamps=False,
                vad_filter=True,
                vad_parameters=dict(min_speech_duration_ms=250, speech_pad_ms=400)
            )
            return list(segments), info
            
        try:
            segments, info = await asyncio.to_thread(run_transcribe)
        except Exception as e:
            if "cuda" in str(e).lower() or "dll" in str(e).lower() or "cublas" in str(e).lower():
                self._log("Lỗi thực thi trên GPU, tự động chuyển sang mô hình CPU (small)...")
                self.load_model()
                segments, info = await asyncio.to_thread(run_transcribe)
            else:
                raise e

        self._log(f"Nhận diện giọng nói hoàn tất! Ngôn ngữ phát hiện: '{info.language}' với độ tin cậy {info.language_probability:.2f}")
        self.schedule_auto_unload()

        srt_content = self.generate_srt_content(segments, offset=offset)
        return srt_content, info.language
