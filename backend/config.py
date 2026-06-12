import os
import sys
import shutil
import importlib.util

# Đảm bảo stdout và stderr sử dụng UTF-8 trên Windows tránh lỗi in Emojis / Tiếng Việt gây sập luồng
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding='utf-8')

from pathlib import Path
from dotenv import load_dotenv

# Tải các biến môi trường từ file .env nếu có
load_dotenv(Path(__file__).resolve().parent / ".env")

# Tự động liên kết các thư viện CUDA từ pip (nvidia-cublas-cu12, nvidia-cudnn-cu12, etc.) vào Windows DLL search path và PATH
if sys.platform == 'win32':
    for pkg in ['nvidia.cublas', 'nvidia.cudnn', 'nvidia.cuda_nvrtc']:
        try:
            spec = importlib.util.find_spec(pkg)
            if spec and spec.submodule_search_locations:
                pkg_path = list(spec.submodule_search_locations)[0]
                bin_path = os.path.join(pkg_path, 'bin')
                if os.path.exists(bin_path):
                    os.add_dll_directory(bin_path)
                    os.environ["PATH"] = bin_path + os.pathsep + os.environ["PATH"]
                elif os.path.exists(pkg_path):
                    os.add_dll_directory(pkg_path)
                    os.environ["PATH"] = pkg_path + os.pathsep + os.environ["PATH"]
        except (ModuleNotFoundError, Exception):
            pass

# Tự động phát hiện và thêm FFmpeg từ WinGet vào PATH nếu chưa có
if not shutil.which("ffmpeg"):
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        winget_packages = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if winget_packages.exists():
            ffmpeg_bins = list(winget_packages.glob("**/ffmpeg-*-full_build/bin")) or list(winget_packages.glob("**/bin"))
            for bin_path in ffmpeg_bins:
                if (bin_path / "ffmpeg.exe").exists():
                    os.environ["PATH"] += os.pathsep + str(bin_path)
                    break

BASE_DIR = Path(__file__).resolve().parent.parent

# Cấu hình đường dẫn xuất dữ liệu
OUTPUT_DIR = BASE_DIR / "output"
TEMP_DIR = BASE_DIR / "temp"
MEMORIES_DIR = BASE_DIR / "memory_db"

# Tạo các thư mục nếu chưa tồn tại
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)
MEMORIES_DIR.mkdir(parents=True, exist_ok=True)

# --- Cấu hình AI & Bộ não ---
# DeepSeek API Configuration
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")

# Ollama Configuration (Local)
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
# Với GPU Quadro 24GB, khuyến khích sử dụng deepseek-r1:14b hoặc deepseek-r1:8b để đạt hiệu quả cao nhất
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "deepseek-r1:8b")

# Quyết định sử dụng phương thức nào (API hoặc local)
# Mặc định ưu tiên API nếu có API Key, ngược lại dùng Ollama cục bộ
USE_OLLAMA = os.getenv("USE_OLLAMA", "").lower() == "true" or not DEEPSEEK_API_KEY

# --- Cấu hình Subtitle & Phụ đề ---
# Vì bạn sở hữu GPU RTX Quadro 6000 24GB VRAM cực mạnh:
# - Mặc định sử dụng model "large-v3" cho độ chính xác cao nhất.
# - Device sẽ là "cuda" để xử lý siêu tốc trên GPU.
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "large-v3")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda") # Có thể đổi thành "cpu" nếu cần
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "float16") # float16 tối ưu cho GPU, int8 cho CPU

# Cấu hình chất lượng tải video để tối ưu hóa thời gian tải và render phụ đề
# Có thể chọn: "720p" (mặc định cho siêu tốc) hoặc "best" (chất lượng gốc tốt nhất)
DOWNLOAD_QUALITY = os.getenv("DOWNLOAD_QUALITY", "best")

# Đường dẫn tuyệt đối tới file cookies.txt (dùng cho yt-dlp vượt bot-check YouTube)
COOKIES_FILE = BASE_DIR / "cookies.txt"

# Cấu hình tăng tốc FFmpeg bằng GPU NVIDIA (h264_nvenc)
FFMPEG_USE_GPU = os.getenv("FFMPEG_USE_GPU", "true").lower() == "true"

# --- Cấu hình Trình duyệt (Playwright) ---
PLAYWRIGHT_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "false").lower() == "true" # Xem trực tiếp AI lướt web
BROWSER_TIMEOUT = int(os.getenv("BROWSER_TIMEOUT", "30000")) # ms

# --- Cấu hình tự giải phóng VRAM (Auto-Unload Whisper) ---
# Tự động giải phóng mô hình Whisper khỏi bộ nhớ GPU CUDA sau X giây không hoạt động
WHISPER_UNLOAD_TIMEOUT = int(os.getenv("WHISPER_UNLOAD_TIMEOUT", "300")) # 300 giây = 5 phút

# --- Cấu hình mặc định cho Style Phụ Đề ---
SUBTITLE_DEFAULT_STYLE = os.getenv(
    "SUBTITLE_DEFAULT_STYLE", 
    "FontSize=16,PrimaryColour=&H00FFFF,OutlineColour=&H000000,BorderStyle=1,Fontname=Outfit"
)
