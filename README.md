# Hệ Thống Nhân Viên AI Tự Động (Aegis AI Employee System)

Hệ thống **Aegis AI Employee** là một tác nhân AI (AI Agent) tự động hóa toàn trình, có khả năng tự nhận thức mục tiêu lớn, lập kế hoạch công việc chi tiết, thực thi đa phương tiện (tải video, tạo và dịch phụ đề, lướt web), và tự rút bài học kinh nghiệm sau mỗi lần vận hành.

Dự án này được tối ưu hóa đặc biệt để tận dụng tối đa sức mạnh xử lý của card đồ họa **NVIDIA RTX Quadro 6000 (24GB VRAM)** để chạy cục bộ các mô hình ngôn ngữ lớn (DeepSeek-R1 qua Ollama) và trích xuất giọng nói tốc độ cao (`faster-whisper` chạy trên CUDA).

---

## 🚀 Các Tính Năng Vượt Trội

1. **Bộ não Trung tâm DeepSeek-R1**: Hỗ trợ chạy local siêu tốc qua Ollama hoặc kết nối qua DeepSeek Cloud API.
2. **Hiển thị Luồng suy nghĩ (Thinking Chain)**: Tách riêng phần lập luận `<think>` của DeepSeek hiển thị thời gian thực lên Dashboard giúp người dùng quan sát được "luồng suy nghĩ" sâu của AI.
3. **Trình Tải Video Cao Cấp**: Tích hợp `yt-dlp` cho phép tải video từ YouTube, TikTok, Facebook kèm báo cáo tiến trình (tốc độ, dung lượng, % hoàn thành) thời gian thực.
4. **Subtitle Engine Siêu Tốc (GPU Powered)**:
   - Sử dụng mô hình `faster-whisper` (chạy trên GPU CUDA dạng `float16` trên 24GB VRAM) nhanh gấp 4 lần phiên bản gốc.
   - Biên dịch phụ đề ngữ cảnh chuyên nghiệp bằng DeepSeek.
   - Tự động khắc phục lỗi ký tự đường dẫn đặc biệt của Windows bằng giải thuật chuyển thư mục làm việc tương đối trong FFmpeg để nhúng phụ đề vào video gốc.
5. **Browser Agent Tự Sửa Sai (Self-Correcting)**:
   - Lướt web bằng Playwright, tự động chụp ảnh màn hình và phân tích cây thư mục tương tác DOM.
   - Chụp ảnh màn hình trực tiếp và stream ảnh Base64 lên Dashboard thời gian thực để người dùng xem AI đang thao tác gì trên web.
6. **Bộ lọc An toàn & Kiểm duyệt (Human-in-the-loop)**: AI tự phát hiện hành động mang tính rủi ro bảo mật (gửi biểu mẫu, click click nút hành động...) và sẽ dừng lại, kích hoạt màn hình kiểm duyệt để chờ người dùng phê duyệt (Approve) hoặc từ chối kèm chỉ dẫn (Reject) qua Dashboard.
7. **Vòng lặp Tự học (Self-Learning Loop)**: Lưu trữ kết quả và bài học kinh nghiệm vào **ChromaDB Vector Store** (có cơ chế tự động chuyển sang CSDL JSON File dự phòng siêu bền nếu môi trường Windows bị thiếu thư viện C++).

---

## 🛠️ Kiến Trúc Hệ Thống

```
Duan1/
├── backend/
│   ├── main.py                 # Điểm khởi chạy máy chủ FastAPI (REST & WebSockets)
│   ├── config.py               # Quản lý cấu hình toàn cục (Đường dẫn, Mô hình, Thiết bị CUDA)
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
└── README.md                   # Tài liệu hướng dẫn sử dụng (Hiện tại)
```

---

## ⚙️ Hướng Dẫn Cài Đặt & Vận Hành

### Bước 1: Chuẩn bị Môi trường GPU (Khuyên dùng)
Vì bạn sở hữu card đồ họa **RTX Quadro 6000 (24GB VRAM)**, hãy đảm bảo hệ thống đã cài đặt:
1. **NVIDIA Driver** phiên bản mới nhất.
2. **CUDA Toolkit** (Phiên bản khuyến nghị: `11.8` hoặc `12.1`).
3. **cuDNN** tương ứng với phiên bản CUDA đã cài để tối ưu hóa thư viện `faster-whisper`.
4. Cài đặt công cụ dòng lệnh **FFmpeg** trên máy của bạn (và thêm vào biến môi trường PATH của hệ thống).

### Bước 2: Cài đặt các thư viện Python
Mở Command Prompt / PowerShell tại thư mục `backend/` và thực hiện lệnh cài đặt:

```bash
cd backend
pip install -r requirements.txt
```

Sau đó, cài đặt trình duyệt tự động của Playwright:
```bash
python -m playwright install chromium
```

### Bước 3: Cấu hình Mô hình & Kết nối (Tùy chọn)
Tạo file `.env` bên trong thư mục `backend/` nếu bạn muốn tùy biến:
```env
# Nếu sử dụng API đám mây chính thức của DeepSeek:
DEEPSEEK_API_KEY=your_api_key_here
USE_OLLAMA=false

# Nếu sử dụng local Ollama (Mặc định được khuyến nghị nhờ có GPU Quadro 24GB VRAM):
USE_OLLAMA=true
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=deepseek-r1:8b  # Hoặc deepseek-r1:14b / 32b tùy nhu cầu

# Cấu hình phụ đề (GPU CUDA float16 được đặt làm mặc định trong config.py)
WHISPER_MODEL_SIZE=large-v3
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16
```

> [!TIP]
> Với 24GB VRAM, bạn hoàn toàn có thể chạy mô hình **`deepseek-r1:14b`** hoặc **`deepseek-r1:32b`** trên Ollama cục bộ siêu mượt và chính xác!

### Bước 4: Khởi chạy Máy chủ Backend
Tại thư mục `backend/`, khởi chạy server:
```bash
python main.py
```
Máy chủ FastAPI sẽ hoạt động tại địa chỉ: `http://127.0.0.1:8000` và mở cổng kết nối WebSocket thời gian thực tại `ws://127.0.0.1:8000/ws`.

### Bước 5: Mở Giao diện Dashboard (Frontend)
Vì giao diện được xây dựng theo kiến trúc **Single Page App (SPA)** tối giản và hiện đại sử dụng HTML/CSS/JS thuần túy, bạn chỉ cần:
1. Click đúp chuột vào file `frontend/index.html` để mở giao diện trực tiếp trên trình duyệt Web (Chrome, Edge, Brave...).
2. Hoặc bạn có thể sử dụng một live-server extension bất kỳ trong IDE (như VS Code Live Server).

---

## 🔮 Hướng Dẫn Trải Nghiệm Hệ Thống

1. **Nhập Mục Tiêu**: Nhập yêu cầu của bạn trên Dashboard (ví dụ: *"Tải một video ngắn về AI từ Youtube, dịch phụ đề sang tiếng Việt và lưu lại"*).
2. **Quan Sát**:
   - Tab **LOG HỆ THỐNG** sẽ hiển thị tiến trình chạy thực tế của các module.
   - Tab **BỘ NÃO SUY NGHĨ** hiển thị từng bước lập luận sâu sắc của DeepSeek.
   - **Task Roadmap** sẽ cập nhật tiến độ, tô màu xanh lá sáng bừng cho các bước hoàn thành.
3. **Màn Hình Livestream**: Khi AI lướt web, bạn sẽ thấy screenshot màn hình trình duyệt nhảy theo từng giây. Khi AI nhúng phụ đề video thành công, một trình phát video sẽ hiện ra ngay lập tức cho phép bạn xem và nghe trực tiếp.
4. **Kiểm duyệt (Human-in-the-loop)**: Thử lập kế hoạch cho một bước duyệt web nhạy cảm, giao diện sẽ tự động hiện modal cảnh báo bảo mật. Bạn có thể nhấn **Approve** để AI tiếp tục hoặc **Reject** kèm bình luận để AI đổi hướng làm việc!
