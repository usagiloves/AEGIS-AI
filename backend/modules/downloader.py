import os
import asyncio
import yt_dlp
from pathlib import Path
from backend.config import OUTPUT_DIR, TEMP_DIR, DOWNLOAD_QUALITY, COOKIES_FILE

class VideoDownloader:
    def __init__(self, progress_callback=None):
        """
        Khởi tạo Downloader.
        :param progress_callback: Hàm callback nhận dict thông tin tiến độ để gửi lên WebSocket.
        """
        self.progress_callback = progress_callback
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = None

    def _progress_hook(self, d):
        if self.progress_callback and d['status'] == 'downloading':
            # Tính toán phần trăm hoàn thành
            total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded_bytes = d.get('downloaded_bytes', 0)
            
            percentage = 0
            if total_bytes > 0:
                percentage = round((downloaded_bytes / total_bytes) * 100, 2)
            
            speed = d.get('speed', 0) # bytes/second
            speed_mb = round(speed / (1024 * 1024), 2) if speed else 0
            
            eta = d.get('eta', 0) # seconds
            
            info = {
                "status": "downloading",
                "filename": os.path.basename(d.get('filename', '')),
                "percentage": percentage,
                "downloaded_mb": round(downloaded_bytes / (1024 * 1024), 2),
                "total_mb": round(total_bytes / (1024 * 1024), 2) if total_bytes else 0,
                "speed_mb": speed_mb,
                "eta": eta
            }
            # Gọi callback (thường chạy trong async loop)
            if asyncio.iscoroutinefunction(self.progress_callback):
                if self.loop and self.loop.is_running():
                    asyncio.run_coroutine_threadsafe(self.progress_callback(info), self.loop)
                else:
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            asyncio.run_coroutine_threadsafe(self.progress_callback(info), loop)
                        else:
                            loop.run_until_complete(self.progress_callback(info))
                    except RuntimeError:
                        asyncio.run(self.progress_callback(info))
            else:
                self.progress_callback(info)
        elif self.progress_callback and d['status'] == 'finished':
            info = {
                "status": "finished",
                "filename": os.path.basename(d.get('filename', '')),
                "percentage": 100
            }
            if asyncio.iscoroutinefunction(self.progress_callback):
                if self.loop and self.loop.is_running():
                    asyncio.run_coroutine_threadsafe(self.progress_callback(info), self.loop)
                else:
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            asyncio.run_coroutine_threadsafe(self.progress_callback(info), loop)
                        else:
                            loop.run_until_complete(self.progress_callback(info))
                    except RuntimeError:
                        asyncio.run(self.progress_callback(info))
            else:
                self.progress_callback(info)

    def _resolve_cookies(self, url):
        """
        Lựa chọn tệp cookie phù hợp dựa trên URL nguồn để tránh giới hạn băng thông hoặc chặn bot.
        """
        from backend.config import BASE_DIR, COOKIES_FILE
        cookies_bili_path = BASE_DIR / "cookies2.txt"
        cookies_default_path = COOKIES_FILE
        
        selected_cookies = None
        if "bilibili.com" in url or "bili" in url:
            if cookies_bili_path.exists():
                selected_cookies = str(cookies_bili_path)
                print(f"[Downloader] 🍪 Đã phát hiện URL Bilibili. Sử dụng file cookie riêng biệt: cookies2.txt")
                
        if not selected_cookies and cookies_default_path.exists():
            selected_cookies = str(cookies_default_path)
            
        return selected_cookies

    def get_info(self, url):
        """
        Lấy thông tin tiêu đề, mô tả và thời lượng của video trước khi tải.
        """
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'js_runtimes': {'node': {}},
            'retries': 10,
            'fragment_retries': 10,
            'file_access_retries': 10,
        }
        selected_cookies = self._resolve_cookies(url)
        if selected_cookies:
            ydl_opts['cookiefile'] = selected_cookies
        else:
            # Tự động trích xuất cookie từ trình duyệt Edge để vượt qua bot-check
            ydl_opts['cookiesfrombrowser'] = ('edge',)
            
        # Tối ưu hóa tiêu đề HTTP riêng cho Bilibili để tránh bị chặn hoặc ngắt kết nối đột ngột
        if "bilibili.com" in url or "bili" in url:
            ydl_opts['http_headers'] = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': 'https://www.bilibili.com/',
                'Accept': '*/*',
                'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
            }
            
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
                return {
                    "title": info.get("title", "Unknown"),
                    "duration": info.get("duration", 0),
                    "thumbnail": info.get("thumbnail", ""),
                    "description": info.get("description", "")[:500] + "..." if info.get("description") else ""
                }
            except Exception as e:
                raise Exception(f"Không thể lấy thông tin video: {str(e)}")

    def download(self, url, output_filename=None, quality=None):
        """
        Tải video (đồng bộ, nên được gọi qua asyncio.to_thread).
        """
        target_dir = str(OUTPUT_DIR)
        
        # Chọn chất lượng tải về để tối ưu tốc độ tải và render phụ đề
        current_quality = quality or os.getenv("DOWNLOAD_QUALITY", DOWNLOAD_QUALITY)
        if current_quality == "720p":
            video_format = 'bestvideo[height<=720]+bestaudio/best[height<=720]/best'
        else:
            video_format = 'bestvideo+bestaudio/best'
            
        import shutil
        ffmpeg_exe = shutil.which("ffmpeg")
            
        ydl_opts = {
            'format': video_format,
            'outtmpl': os.path.join(target_dir, output_filename or '%(title)s.%(ext)s'),
            'progress_hooks': [self._progress_hook],
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'js_runtimes': {'node': {}},
            'retries': 10,
            'fragment_retries': 10,
            'file_access_retries': 10,
        }
        if ffmpeg_exe:
            ydl_opts['ffmpeg_location'] = str(Path(ffmpeg_exe).parent)
        
        # Lựa chọn tệp cookie phù hợp dựa trên URL nguồn để vượt qua bot-check
        selected_cookies = self._resolve_cookies(url)
        if selected_cookies:
            ydl_opts['cookiefile'] = selected_cookies
        else:
            # Tự động trích xuất cookie từ trình duyệt Edge để vượt qua bot-check
            ydl_opts['cookiesfrombrowser'] = ('edge',)
            
        # Áp dụng giới hạn tốc độ tải và giả lập trình duyệt đối với Bilibili tránh sập luồng
        if "bilibili.com" in url or "bili" in url:
            ydl_opts['ratelimit'] = 3 * 1024 * 1024 # Giới hạn tốc độ tải về tối đa 3MB/s tránh Bilibili ngắt kết nối
            ydl_opts['http_headers'] = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': 'https://www.bilibili.com/',
                'Accept': '*/*',
                'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
            }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=True)
                downloaded_file = ydl.prepare_filename(info)
                # Đảm bảo đuôi mở rộng là mp4 sau khi merge
                if not downloaded_file.endswith('.mp4'):
                    base, _ = os.path.splitext(downloaded_file)
                    downloaded_file = base + '.mp4'
                
                return {
                    "success": True,
                    "filepath": downloaded_file,
                    "title": info.get("title", "Unknown"),
                    "duration": info.get("duration", 0)
                }
            except Exception as e:
                return {
                    "success": False,
                    "error": str(e)
                }

# Demo usage
async def main_demo():
    def my_cb(info):
        print(f"Tiến độ: {info}")
    
    downloader = VideoDownloader(progress_callback=my_cb)
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    print("Đang lấy thông tin...")
    meta = downloader.get_info(url)
    print(f"Tiêu đề: {meta['title']}")
    print("Đang tải xuống...")
    res = await asyncio.to_thread(downloader.download, url)
    print(f"Kết quả: {res}")

if __name__ == "__main__":
    asyncio.run(main_demo())
