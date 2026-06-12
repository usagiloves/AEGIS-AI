import os
import asyncio
import traceback
from pathlib import Path
from backend.config import OUTPUT_DIR, FFMPEG_USE_GPU

class VideoClipper:
    def __init__(self, progress_callback=None):
        self.progress_callback = progress_callback

    def log(self, message):
        if self.progress_callback:
            self.progress_callback(message)
        else:
            print(f"[Clipper] {message}")

    async def create_short(self, video_path, duration=60, aspect_ratio="9:16"):
        """
        Cắt video và chuyển đổi khung hình thành Shorts/TikTok (9:16) chất lượng cao bằng FFmpeg.
        """
        try:
            self.log(f"Khởi chạy cắt video Shorts ({duration}s, tỉ lệ {aspect_ratio})...")
            
            output_filename = f"short_{aspect_ratio.replace(':', 'x')}_{Path(video_path).name}"
            final_output_path = Path(OUTPUT_DIR) / output_filename
            
            # Xây dựng bộ lọc FFmpeg crop dựa trên tỉ lệ mong muốn
            # ih*9/16:ih: (giữ chiều cao gốc ih, cắt chiều rộng iw)
            crop_filter = ""
            if aspect_ratio == "9:16":
                self.log("Đang cấu hình căn giữa và cắt khung dọc 9:16 (TikTok/Shorts)...")
                crop_filter = "crop=ih*9/16:ih:(iw-ow)/2:0"
            elif aspect_ratio == "1:1":
                self.log("Đang cấu hình căn giữa và cắt khung hình vuông 1:1...")
                crop_filter = "crop=ih:ih:(iw-ow)/2:0"
            
            # Tham số ffmpeg cắt khoảng thời gian và mã hóa lại để giữ chất lượng
            cmd = ["ffmpeg", "-y", "-ss", "00:00:00", "-i", str(video_path)]
            
            if crop_filter:
                cmd += ["-vf", crop_filter]
                
            cmd += [
                "-t", str(duration),
                "-c:v", "libx264",
                "-profile:v", "high",
                "-level", "4.2",
                "-preset", "veryfast",
                "-crf", "22",
                "-pix_fmt", "yuv420p",  # Đảm bảo định dạng màu tương thích 100% tất cả trình duyệt HTML5
                "-movflags", "+faststart",  # Cơ chế FastStart cho phép phát video streaming mượt mà tức thì
                "-c:a", "aac",
                "-b:a", "128k",
                str(final_output_path)
            ]
            
            self.log("Đang thực thi bộ lọc FFmpeg mã hóa Shorts...")
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                self.log("Trích xuất Shorts/TikTok video thành công!")
                return {"success": True, "filepath": str(final_output_path)}
            else:
                error_log = stderr.decode('utf-8', errors='ignore')
                self.log(f"Lỗi FFmpeg khi cắt video: {error_log}")
                return {"success": False, "error": error_log}

        except Exception as e:
            traceback.print_exc()
            self.log(f"Gặp sự cố khi cắt video: {str(e)}")
            return {"success": False, "error": str(e)}
