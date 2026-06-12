# ✦ AEGIS AI EMPLOYEE SYSTEM ✦
### Trung Tâm Điều Hành Tác Nhân Tự Động Hóa & Đa Phương Tiện (Advanced AI Employee Workflow Center)

Hệ thống **Aegis AI Employee** là một tác nhân AI (AI Agent) tự động hóa toàn trình, tích hợp sơ đồ node-graph trực quan giúp người dùng dễ dàng kết nối, kéo thả và tùy biến các khối chức năng để điều khiển toàn bộ pipeline xử lý đa phương tiện. Hệ thống có khả năng tự nhận thức mục tiêu, lập kế hoạch chi tiết, tương tác trực tuyến qua trình duyệt tự động, và tự rút bài học kinh nghiệm sau mỗi chu trình vận hành.

Dự án được thiết kế tối ưu để tận dụng tối đa sức mạnh phần cứng GPU (khuyên dùng các dòng card chuyên dụng như **NVIDIA RTX Quadro 6000 24GB VRAM** hoặc tương đương) để chạy cục bộ các mô hình ngôn ngữ lớn (DeepSeek-R1 qua Ollama) và mô hình nhận diện giọng nói tốc độ cao (`faster-whisper` trên nền CUDA).

---

## ⚡ Các Tính Năng Vượt Trội

1. **Sơ Đồ Workflow Node-Graph Trực Quan**: Cấu hình quy trình xử lý đa bước (Downloader, Audio Enhance, ASR, Translation, TTS, Exporter, SEO, Clipper, Publisher) bằng cách kết nối các node trên giao diện canvas kéo thả mượt mà.
2. **Bộ Não Trung Tâm DeepSeek-R1**: Hỗ trợ chạy local siêu tốc qua Ollama hoặc kết nối API đám mây, cho phép AI suy nghĩ (Thinking Chain) và lập kế hoạch tối ưu.
3. **Hiển Thị Luồng Lập Luận (Thinking Chain)**: Tách riêng phần lập luận `<think>` hiển thị thời gian thực lên Dashboard giúp người dùng theo dõi cách AI phân tích yêu cầu.
4. **Trình Tải Video Cao Cấp**: Tích hợp `yt-dlp` cho phép tải video từ YouTube, Bilibili, TikTok kèm báo cáo tiến trình (tốc độ, dung lượng, % hoàn thành) thời gian thực.
5. **Động Cơ Subtitle Engine Siêu Tốc (CUDA)**:
   * Trích xuất giọng nói bằng mô hình `faster-whisper` chạy trực tiếp trên GPU CUDA (dạng `float16` giúp tiết kiệm tài nguyên và tăng tốc gấp 4 lần).
   * Tự động sửa lỗi hiển thị ký tự đặc biệt của Windows và đường dẫn tương đối khi nhúng phụ đề bằng FFmpeg.
   * Biên dịch phụ đề ngữ cảnh thông minh và hỗ trợ gộp phụ đề song ngữ (Dual Subtitles).
6. **Browser Agent Livestream**: Lướt web tự động bằng Playwright, tự sửa sai khi gặp lỗi phần tử DOM, đồng thời chụp ảnh màn hình truyền trực tiếp (Base64 Stream) lên giao diện Dashboard.
7. **Kiểm Duyệt An Toàn (Human-in-the-loop)**: Tự động dừng lại và hiển thị modal phê duyệt bảo mật khi AI chuẩn bị thực hiện các thao tác nhạy cảm hoặc trước khi xuất bản/đăng tải thành phẩm.
8. **Vòng Lặp Tự Học (Self-Learning Loop)**: Tích hợp ChromaDB và CSDL tệp tin JSON dự phòng để lưu trữ bài học kinh nghiệm sau mỗi lần chạy, giúp AI ngày càng thông minh hơn.

---

## 📂 Kiến Trúc Hệ Thống

```
AEGIS-AI/
├── backend/
│   ├── main.py                 # Điểm khởi chạy máy chủ FastAPI (REST & WebSockets)
│   ├── config.py               # Quản lý cấu hình toàn cục (Đường dẫn, mô hình, CUDA)
│   ├── telegram_bot.py         # Bot điều khiển và thông báo qua ứng dụng Telegram
│   ├── requirements.txt        # Các thư viện Python cần thiết
│   └── modules/
│       ├── orchestrator.py     # Bộ điều phối trung tâm, xử lý vòng lặp suy nghĩ và HITL
│       ├── planner.py          # Trình lập kế hoạch và chia nhỏ mục tiêu của DeepSeek
│       ├── downloader.py       # Module tải video sử dụng yt-dlp
│       ├── subtitle.py         # Trích xuất phụ đề (faster-whisper CUDA) + Dịch + FFmpeg
│       ├── browser.py          # Trình duyệt tự động Playwright (hỗ trợ livestream màn hình)
│       └── learning.py         # Quản lý bộ nhớ kinh nghiệm (ChromaDB hoặc JSON File)
├── frontend/
│   ├── index.html              # Giao diện Dashboard (Glassmorphism Dark Mode)
│   ├── style.css               # Thiết kế giao diện Glassmorphism và hiệu ứng ánh sáng
│   └── app.js                  # Xử lý luồng WebSocket và cập nhật giao diện trực quan
├── output/                     # Thư mục lưu trữ video thành phẩm (.mp4, .srt)
└── README.md                   # Tài liệu hướng dẫn sử dụng (Hiện tại)
```

---

## 🛠️ Hướng Dẫn Cài Đặt & Cấu Hình

### Yêu Cầu Hệ Thống
* **Hệ điều hành**: Windows 10/11 hoặc Linux.
* **Python**: Phiên bản khuyến nghị `3.10.x`.
* **Phần cứng (Khuyên dùng cho GPU)**:
  * NVIDIA GPU hỗ trợ CUDA (Khuyên dùng >= 8GB VRAM, tối ưu trên dòng 24GB VRAM).
  * Đã cài đặt **CUDA Toolkit** (ví dụ `11.8` hoặc `12.1`) và thư viện **cuDNN** tương ứng.
* **Công cụ**: **FFmpeg** đã được cài đặt và thêm vào biến môi trường `PATH` của hệ thống.

### Bước 1: Clone dự án và truy cập thư mục
```bash
git clone https://github.com/usagiloves/AEGIS-AI.git
cd AEGIS-AI
```

### Bước 2: Cài đặt các thư viện Python
Nên tạo một môi trường ảo để cài đặt sạch sẽ các thư viện:
```bash
# Tạo môi trường ảo (tùy chọn)
python -m venv venv
venv\Scripts\activate

# Cài đặt các thư viện yêu cầu
pip install -r backend/requirements.txt
```

Cài đặt các trình duyệt cần thiết cho Playwright:
```bash
python -m playwright install chromium
```

### Bước 3: Cấu hình biến môi trường
Tạo tệp `.env` bên trong thư mục `backend/` để cấu hình mô hình AI:

```env
# Nếu sử dụng Ollama cục bộ (Khuyên dùng với GPU cấu hình cao):
USE_OLLAMA=true
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=deepseek-r1:8b  # Có thể đổi thành deepseek-r1:14b / 32b nếu VRAM cho phép

# Nếu sử dụng API đám mây chính thức của DeepSeek:
# USE_OLLAMA=false
# DEEPSEEK_API_KEY=your_api_key_here

# Cấu hình Whisper trích xuất phụ đề
WHISPER_MODEL_SIZE=large-v3
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16
```

---

## 🚀 Hướng Dẫn Khởi Chạy

### 1. Khởi chạy máy chủ Backend
Di chuyển vào thư mục `backend/` và chạy lệnh:
```bash
cd backend
python main.py
```
Máy chủ FastAPI sẽ khởi chạy trên cổng **`8000`**. Telegram Bot đi kèm cũng sẽ bắt đầu hoạt động ngầm (nếu được cấu hình token).

### 2. Truy cập giao diện điều khiển (Frontend)
Hệ thống sử dụng kiến trúc SPA và được mount trực tiếp qua server backend. Bạn chỉ cần truy cập:
👉 **[http://127.0.0.1:8000/app/index.html](http://127.0.0.1:8000/app/index.html)** (hoặc link ngắn **[http://127.0.0.1:8000/](http://127.0.0.1:8000/)**) trên bất kỳ trình duyệt nào.

---

## 💡 Hướng Dẫn Trải Nghiệm Quy Trình

1. **Nhập Mục Tiêu Lớn**: Tại ô nhập mục tiêu ở Header, điền yêu cầu vận hành (Ví dụ: *"Tải video ngắn từ youtube https://www.youtube.com/watch?v=xxxx, dịch sang tiếng Việt và nhúng phụ đề song ngữ"*).
2. **Thiết Lập Sơ Đồ Node**: Bật/Tắt các Node chức năng (như `Downloader`, `Translator`, `Exporter`, `SEO`, `Clipper`, `Uploader`) bằng cách bấm nút `✕`/`👁️` trên góc mỗi Node. Bấm trực tiếp vào từng Node để cấu hình tham số nâng cao.
3. **Bấm Bắt Đầu Vận Hành**: Hệ thống sẽ chuyển trạng thái sang lập kế hoạch, hiển thị luồng suy nghĩ của DeepSeek trên bảng điều khiển bên trái và chạy tuần tự các Node trên canvas đồ họa.
4. **Phê Duyệt HITL**: Khi quy trình chạy tới bước đăng tải hoặc thao tác trình duyệt nhạy cảm, giao diện sẽ xuất hiện Modal Kiểm Duyệt. Bạn có thể xem trước nội dung, chỉnh sửa tiêu đề và mô tả SEO do AI tạo ra, sau đó nhấn **Approve** (Đồng ý) hoặc **Reject** (Từ chối kèm chỉ dẫn sửa lại).
5. **Thành Phẩm**: Video đầu ra được lưu trữ tại `output/`. Bạn có thể nhấp trực tiếp vào danh sách video ở mục **Thư Viện Thành Phẩm** để xem trực tiếp hoặc tải xuống.
