import os
import httpx
import json
import re
import asyncio
import traceback
import sys
import threading
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from backend.config import (
    DEEPSEEK_API_KEY, DEEPSEEK_API_BASE, 
    OLLAMA_HOST, OLLAMA_MODEL, USE_OLLAMA, TEMP_DIR
)

# --- Thiết Lập Structured Logging ---
LOG_DIR = Path(TEMP_DIR) / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "system.log"

logger = logging.getLogger("AegisAI")
logger.setLevel(logging.INFO)
# Tránh bị nhân đôi handler khi hot-reload
if not logger.handlers:
    # Lưu tệp xoay vòng: tối đa 5MB mỗi file, giữ lại tối đa 3 file cũ
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter('%(asctime)s - [%(levelname)s] - %(message)s'))
    logger.addHandler(file_handler)
    
    # Ghi ra console
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter('[Aegis] %(levelname)s: %(message)s'))
    logger.addHandler(console_handler)

async def run_async_in_new_thread(coro_func, *args, **kwargs):
    """
    Chạy một hàm coroutine trên một luồng phụ mới với Event Loop riêng (sử dụng ProactorEventLoop trên Windows).
    Giúp giải quyết triệt để lỗi NotImplementedError của Playwright khi chạy dưới SelectorEventLoop của Uvicorn.
    """
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def thread_target():
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        try:
            coro = coro_func(*args, **kwargs)
            res = new_loop.run_until_complete(coro)
            loop.call_soon_threadsafe(future.set_result, res)
        except Exception as e:
            loop.call_soon_threadsafe(future.set_exception, e)
        finally:
            new_loop.close()

    thread = threading.Thread(target=thread_target, daemon=True)
    thread.start()
    return await future

# Toàn cục chứa cấu hình API hiện tại do người dùng cấu hình từ UI
current_api_config = {
    "provider": "ollama" if USE_OLLAMA else "deepseek",
    "api_base": OLLAMA_HOST if USE_OLLAMA else DEEPSEEK_API_BASE,
    "api_key": "" if USE_OLLAMA else DEEPSEEK_API_KEY,
    "model": OLLAMA_MODEL if USE_OLLAMA else "deepseek-chat",
    "subtitle_style": "", # Style tùy biến từ giao diện người dùng
    "subtitle_offset": 0.0, # Độ lệch thời gian phụ đề mặc định
    "modules_mode": "local", # "local" hoặc "remote"
    "remote_api_base": "http://127.0.0.1:8000", # URL gốc của các API microservices
    "upload_youtube": False,
    "privacy_status": "private"
}


# Store active web socket clients to broadcast events globally
active_sockets = set()
event_listeners = []

async def broadcast_event(event_type: str, data: dict):
    """
    Gửi thông tin cập nhật thời gian thực tới tất cả các client đang kết nối Dashboard.
    Đồng thời tự động ghi nhận các sự kiện log vào tệp cục bộ xoay vòng.
    """
    # Tự động ghi vào Structured Logger nếu là sự kiện log hệ thống
    if event_type == "log":
        msg = data.get("message", "")
        level = data.get("level", "info")
        if level == "info":
            logger.info(msg)
        elif level == "success":
            logger.info(f"SUCCESS: {msg}")
        elif level == "warning":
            logger.warning(msg)
        elif level == "error":
            logger.error(msg)
        else:
            logger.info(msg)

    payload = json.dumps({"type": event_type, "data": data})
    if active_sockets:
        # Tạo bản sao của tập hợp để tránh lỗi sửa đổi tập hợp khi đang lặp
        await asyncio.gather(
            *[client.send_text(payload) for client in list(active_sockets)],
            return_exceptions=True
        )

    # Đẩy sự kiện qua các listener đăng ký thêm (ví dụ: Telegram Bot)
    for listener in list(event_listeners):
        try:
            if asyncio.iscoroutinefunction(listener):
                await listener(event_type, data)
            else:
                listener(event_type, data)
        except Exception as e:
            print(f"[EventBus] Lỗi khi chuyển tiếp sự kiện tới listener: {str(e)}")

def extract_thinking(text: str):
    """
    Trích xuất phần suy nghĩ nằm trong thẻ <think>...</think> của DeepSeek-R1.
    Trả về (thinking, clean_text).
    """
    thinking = ""
    clean_text = text
    match = re.search(r'<think>(.*?)</think>', text, re.DOTALL)
    if match:
        thinking = match.group(1).strip()
        clean_text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    return thinking, clean_text

async def call_llm(prompt: str, system_prompt: str = None) -> str:
    """
    Gọi LLM (Ollama, DeepSeek Cloud hoặc bất kỳ API tương thích OpenAI nào cấu hình thủ công) và trích xuất kết quả.
    Đẩy phần <think> lên Dashboard thời gian thực để người dùng xem AI suy nghĩ.
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    response_text = ""
    
    provider = current_api_config.get("provider", "ollama")
    api_base = current_api_config.get("api_base", "").strip()
    api_key = current_api_config.get("api_key", "").strip()
    model = current_api_config.get("model", "").strip()

    if provider == "ollama":
        url = f"{api_base}/api/chat" if not api_base.endswith("/api/chat") else api_base
        if not api_base:
            url = f"{OLLAMA_HOST}/api/chat"
            
        payload = {
            "model": model or OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.2
            }
        }
        
        await broadcast_event("log", {"level": "info", "message": f"Đang gọi mô hình local {model or OLLAMA_MODEL} qua Ollama..."})
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                res_json = resp.json()
                response_text = res_json['message']['content']
        except httpx.HTTPStatusError as e:
            err_body = ""
            try:
                err_body_json = e.response.json()
                if "error" in err_body_json and isinstance(err_body_json["error"], str):
                    err_body = f" - Chi tiết: {err_body_json['error']}"
                elif "error" in err_body_json and isinstance(err_body_json["error"], dict) and "message" in err_body_json["error"]:
                    err_body = f" - Chi tiết: {err_body_json['error']['message']}"
                else:
                    err_body = f" - Chi tiết phản hồi: {e.response.text}"
            except Exception:
                try:
                    err_body = f" - Chi tiết phản hồi: {e.response.text}"
                except Exception:
                    pass
            error_msg = f"Lỗi HTTP {e.response.status_code} khi gọi Ollama: {str(e)}{err_body}"
            await broadcast_event("log", {"level": "error", "message": error_msg})
            raise RuntimeError(error_msg) from e
        except Exception as e:
            await broadcast_event("log", {"level": "error", "message": f"Lỗi hệ thống hoặc kết nối khi gọi Ollama: {str(e)}"})
            raise e
    elif provider == "duan2":
        url = api_base
        if not url:
            url = "http://localhost:3000"
            
        url = f"{url.rstrip('/')}/api/ai/chat"
        
        headers = {
            "Content-Type": "application/json"
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            
        payload = {
            "messages": messages,
            "model": model or "gpt-4o",
            "temperature": 0.2
        }
        
        await broadcast_event("log", {"level": "info", "message": f"Đang gọi API Cinematic OS (Duan2) qua {url}..."})
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                res_json = resp.json()
                response_text = res_json['content']
        except httpx.HTTPStatusError as e:
            err_body = ""
            try:
                err_body_json = e.response.json()
                if "error" in err_body_json and isinstance(err_body_json["error"], str):
                    err_body = f" - Chi tiết lỗi: {err_body_json['error']}"
                elif "error" in err_body_json and isinstance(err_body_json["error"], dict) and "message" in err_body_json["error"]:
                    err_body = f" - Chi tiết lỗi: {err_body_json['error']['message']}"
                else:
                    err_body = f" - Chi tiết phản hồi: {e.response.text}"
            except Exception:
                try:
                    err_body = f" - Chi tiết phản hồi: {e.response.text}"
                except Exception:
                    pass
            error_msg = f"Lỗi HTTP {e.response.status_code} khi gọi API Duan2: {str(e)}{err_body}"
            await broadcast_event("log", {"level": "error", "message": error_msg})
            raise RuntimeError(error_msg) from e
        except Exception as e:
            await broadcast_event("log", {"level": "error", "message": f"Lỗi hệ thống hoặc kết nối khi gọi API Duan2: {str(e)}"})
            raise e
    else:
        url = api_base
        if not url:
            url = DEEPSEEK_API_BASE
            
        if not url.endswith("/chat/completions"):
            url = f"{url.rstrip('/')}/chat/completions"
            
        headers = {
            "Authorization": f"Bearer {api_key or DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/aegis-ai",
            "X-Title": "Aegis AI Employee"
        }

        resolved_model = model
        if not resolved_model and provider in ("custom", "openrouter"):
            try:
                base_endpoint = api_base.strip().rstrip('/')
                if base_endpoint:
                    if base_endpoint.endswith("/v1"):
                        models_url = f"{base_endpoint}/models"
                    else:
                        models_url = f"{base_endpoint}/v1/models"
                    
                    await broadcast_event("log", {"level": "info", "message": f"Tên model trống. Đang tự động quét danh sách model từ: {models_url}..."})
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        m_resp = await client.get(models_url, headers=headers)
                        if m_resp.status_code == 200:
                            m_data = m_resp.json()
                            if "data" in m_data and isinstance(m_data["data"], list) and len(m_data["data"]) > 0:
                                resolved_model = m_data["data"][0].get("id")
                                await broadcast_event("log", {"level": "info", "message": f"Đã tự động chọn model mặc định từ server: '{resolved_model}'"})
            except Exception as ex:
                await broadcast_event("log", {"level": "warning", "message": f"Không thể tự động nhận diện model: {str(ex)}. Sẽ dùng fallback mặc định."})

        payload = {
            "model": resolved_model or ("deepseek-chat" if provider == "deepseek" else "deepseek/deepseek-r1" if provider == "openrouter" else "custom-model"),
            "messages": messages,
            "temperature": 0.2,
            "stream": False
        }
        
        await broadcast_event("log", {"level": "info", "message": f"Đang gọi API tương thích OpenAI ({payload['model']}) qua {url}..."})
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                res_json = resp.json()
                response_text = res_json['choices'][0]['message']['content']
        except httpx.HTTPStatusError as e:
            err_body = ""
            try:
                err_body_json = e.response.json()
                if "error" in err_body_json and isinstance(err_body_json["error"], str):
                    err_body = f" - Chi tiết lỗi: {err_body_json['error']}"
                elif "error" in err_body_json and isinstance(err_body_json["error"], dict) and "message" in err_body_json["error"]:
                    err_body = f" - Chi tiết lỗi: {err_body_json['error']['message']}"
                else:
                    err_body = f" - Chi tiết phản hồi: {e.response.text}"
            except Exception:
                try:
                    err_body = f" - Chi tiết phản hồi: {e.response.text}"
                except Exception:
                    pass
            error_msg = f"Lỗi HTTP {e.response.status_code} khi gọi API: {str(e)}{err_body}"
            await broadcast_event("log", {"level": "error", "message": error_msg})
            raise RuntimeError(error_msg) from e
        except Exception as e:
            await broadcast_event("log", {"level": "error", "message": f"Lỗi hệ thống hoặc kết nối khi gọi API: {str(e)}"})
            raise e

    thinking, clean_content = extract_thinking(response_text)
    if thinking:
        await broadcast_event("thinking", {"thinking": thinking})
    
    return clean_content


# --- Định nghĩa biến toàn cục điều khiển vòng lặp ---
current_task_status = "idle" # idle, planning, running, waiting_approval, completed, failed, queued
approval_event = asyncio.Event()
approval_decision = None # "approved" hoặc "rejected"
approval_comment = ""
edited_srt_content = ""
approved_seo_title = ""
approved_seo_desc = ""

# Khóa đồng bộ toàn cục bảo vệ GPU tránh sập do chạy song song nhiều task
execution_lock = asyncio.Lock()

class Orchestrator:
    def __init__(self):
        self.current_goal = ""
        self.plan_steps = []
        self.current_step_index = 0
        self.detected_language = "en"
        self.active_nodes_data = {}

    async def execute_goal(self, goal: str, api_config: dict = None):
        """
        Khởi chạy thực hiện một Mục tiêu lớn (Goal).
        Có hỗ trợ cơ chế xếp hàng tuần tự (Task Queue) bảo vệ tài nguyên GPU CUDA.
        """
        global current_task_status, current_api_config
        
        is_queued = execution_lock.locked()
        if is_queued:
            await broadcast_event("log", {"level": "warning", "message": f"[Hàng đợi] Hệ thống đang bận xử lý tác vụ khác. Đã xếp hàng mục tiêu mới: '{goal}'..."})
            await broadcast_event("status_change", {"status": "queued"})
            
        async with execution_lock:
            if api_config:
                for k, v in api_config.items():
                    if v is not None:
                        current_api_config[k] = v
            self.current_goal = goal
            self.current_step_index = 0
            current_task_status = "planning"
            
            await broadcast_event("status_change", {"status": current_task_status})
            await broadcast_event("log", {"level": "info", "message": f"Bắt đầu thực hiện Goal: '{goal}'"})
            
            try:
                # Import muộn để tránh vòng lặp import chéo
                from backend.modules.planner import Planner
                from backend.modules.learning import LearningLoop
                
                # Khởi tạo các module
                planner = Planner()
                learning = LearningLoop()
                
                # --- RAG: Tra cứu kinh nghiệm cũ ---
                await broadcast_event("log", {"level": "info", "message": "Đang tra cứu cơ sở dữ liệu ChromaDB để lấy kinh nghiệm cũ..."})
                past_experiences = learning.search_memories(goal)
                
                # --- Tạo Kế Hoạch ---
                await broadcast_event("log", {"level": "info", "message": "Đang phân tích và lên danh sách công việc bằng DeepSeek..."})
                self.plan_steps = await planner.generate_plan(goal, past_experiences)
                
                await broadcast_event("plan_created", {"steps": self.plan_steps})
                await broadcast_event("log", {"level": "info", "message": f"Kế hoạch gồm {len(self.plan_steps)} bước đã được phê duyệt."})
                
                current_task_status = "running"
                await broadcast_event("status_change", {"status": current_task_status})
                
                # --- Vòng lặp thực thi từng bước ---
                for idx, step in enumerate(self.plan_steps):
                    self.current_step_index = idx
                    await broadcast_event("step_start", {"index": idx})
                    await broadcast_event("log", {"level": "info", "message": f"Đang thực hiện Bước {idx+1}: {step['title']}..."})
                    
                    # Chạy bước và lưu kết quả
                    success, result_message = await self.execute_step(step)
                    
                    if success:
                        step['status'] = 'completed'
                        step['result'] = result_message
                        await broadcast_event("step_complete", {"index": idx, "result": result_message})
                        await broadcast_event("log", {"level": "success", "message": f"Hoàn thành Bước {idx+1}!"})
                    else:
                        step['status'] = 'failed'
                        step['result'] = result_message
                        await broadcast_event("step_fail", {"index": idx, "error": result_message})
                        await broadcast_event("log", {"level": "error", "message": f"Thất bại ở Bước {idx+1}: {result_message}"})
                        
                        # Nếu thất bại, ghi nhớ bài học và dừng lại
                        learning.save_memory(goal, self.plan_steps, success=False, error_msg=result_message)
                        current_task_status = "failed"
                        await broadcast_event("status_change", {"status": current_task_status})
                        return
                
                # Hoàn thành tất cả các bước thành công!
                learning.save_memory(goal, self.plan_steps, success=True)
                current_task_status = "completed"
                await broadcast_event("status_change", {"status": current_task_status})
                await broadcast_event("log", {"level": "success", "message": "Chúc mừng! Hệ thống đã hoàn thành xuất sắc mục tiêu!"})

                # --- AUTO-POST & CLOUD SYNC & SEO WIDGET ---
                # 1. Tìm video kết quả cuối cùng
                final_video_path = None
                for step in reversed(self.plan_steps):
                    if "output_file" in step:
                        final_video_path = step["output_file"]
                        break
                
                # 2. Sinh mô tả SEO, Hashtag và Chapters bằng AI
                # Tìm file srt dịch làm ngữ cảnh
                translated_srt_path = None
                for step in reversed(self.plan_steps):
                    if step.get("module") == "subtitle" and step.get("action") == "translate" and "output_srt_translated" in step:
                        translated_srt_path = step["output_srt_translated"]
                        break
                    elif step.get("module") == "subtitle" and step.get("action") == "transcribe" and "output_srt" in step:
                        translated_srt_path = step["output_srt"]
                        break
                        
                seo_result = ""
                if translated_srt_path and os.path.exists(translated_srt_path):
                    with open(translated_srt_path, 'r', encoding='utf-8') as f:
                        srt_text = f.read()
                    
                    await broadcast_event("log", {"level": "info", "message": "Đang dùng LLM lập luận sinh mô tả SEO, Hashtag xu hướng và chương mục (Chapters)..."})
                    seo_prompt = (
                        "Hãy đóng vai là một nhà sáng tạo nội dung (Content Creator) chuyên nghiệp hàng đầu tại Việt Nam.\n"
                        "Phân tích tệp phụ đề video sau và viết một bài mô tả đăng video cực kỳ tự nhiên, cuốn hút và chuẩn SEO.\n\n"
                        "YÊU CẦU QUAN TRỌNG VỀ VĂN PHONG (ĐỂ ĐẢM BẢO SỰ TỰ NHIÊN NHƯ NGƯỜI THẬT VIẾT):\n"
                        "- TUYỆT ĐỐI TRÁNH giọng điệu rập khuôn, sáo rỗng của AI (tránh các từ như: 'Chào mừng các bạn đã quay trở lại...', 'Trong video ngày hôm nay chúng ta sẽ...', 'vô cùng', 'cực kỳ', 'hãy cùng tôi khám phá...').\n"
                        "- Viết thẳng vào vấn đề bằng một câu hook (mở đầu) gây tò mò, đánh trúng nỗi đau hoặc kích thích người dùng.\n"
                        "- Sử dụng ngôn ngữ giao tiếp đời thường nhưng văn minh, gần gũi (dùng đại từ xưng hô tự nhiên như 'mình', 'bạn', 'cả nhà').\n"
                        "- Hạn chế tối đa việc lạm dụng dấu chấm than (!) và emoji. Chỉ chèn emoji thật tinh tế ở cuối câu để tạo điểm nhấn, không chèn tràn lan ở đầu mỗi dòng.\n"
                        "- Tập trung chia sẻ giá trị cốt lõi của video theo dạng kể chuyện (storytelling) ngắn gọn.\n\n"
                        "BỐ CỤC BÀI VIẾT BẮT BUỘC (PHẢI ĐÚNG CÁC TỪ KHÓA BÊN DƯỚI ĐỂ HỆ THỐNG TỰ ĐỘNG PARSE THÔNG TIN):\n"
                        "TIÊU ĐỀ: [Viết 1 tiêu đề video tiếng Việt giật gân, cực kỳ cuốn hút, chuẩn tỷ lệ click CTR, dài dưới 80 ký tự]\n"
                        "MÔ TẢ:\n"
                        "[Đoạn mô tả ngắn 2-3 câu tự nhiên kể chuyện để hút người xem nhấn nút 'Xem thêm'...]\n"
                        "[Tóm tắt súc tích 3-4 ý chính nổi bật nhất của video...]\n"
                        "CHƯƠNG MỤC:\n"
                        "[Lập bảng mốc thời gian kiểu YouTube, ví dụ: 00:00 - Tiêu đề chương. Bắt buộc phải có mốc 00:00]\n"
                        "HASHTAGS:\n"
                        "[3-5 hashtags xu hướng, liên quan trực tiếp và tự nhiên nhất]\n\n"
                        "LƯU Ý: Trả về định dạng văn bản trực tiếp sạch sẽ (không bọc trong khối ```code```).\n\n"
                        f"Nội dung phụ đề:\n{srt_text[:3000]}"
                    )
                    try:
                        seo_result = await call_llm(seo_prompt, system_prompt="You are an expert AI social media marketer and SEO optimizer.")
                        await broadcast_event("log", {"level": "success", "message": "Đã sinh nội dung SEO và chương mục thành công!"})
                        # Gửi nội dung SEO về cho Dashboard hiển thị
                        await broadcast_event("seo_ready", {"seo_content": seo_result})
                    except Exception as seo_err:
                        await broadcast_event("log", {"level": "warning", "message": f"Không thể tự động sinh SEO: {str(seo_err)}"})
                
                # 3. Thực hiện tải lên Google Drive / Telegram Bot nếu được bật
                if final_video_path and os.path.exists(final_video_path):
                    from backend.modules.uploader import upload_to_google_drive, send_to_telegram
                    
                    if current_api_config.get("upload_gdrive", False):
                        await upload_to_google_drive(final_video_path, progress_callback=lambda msg: broadcast_event("log", msg))
                        
                    if current_api_config.get("upload_telegram", False):
                        telegram_caption = f"🎬 <b>{os.path.basename(final_video_path)}</b> đã hoàn thành!\n\n"
                        if seo_result:
                            telegram_caption += seo_result[:800] + "..."
                        await send_to_telegram(final_video_path, telegram_caption, progress_callback=lambda msg: broadcast_event("log", msg))
                
            except Exception as e:
                traceback.print_exc()
                current_task_status = "failed"
                await broadcast_event("status_change", {"status": current_task_status})
                await broadcast_event("log", {"level": "error", "message": f"Lỗi hệ thống: {str(e)}"})

    async def execute_step(self, step: dict):
        """
        Thực hiện một bước cụ thể bằng cách gọi mô-đun thích hợp.
        """
        global current_task_status, approval_event, approval_decision, approval_comment
        
        module = step.get("module")
        action = step.get("action")
        params = step.get("params", {})
        
        # --- Cơ chế Human-in-the-loop (HITL) ---
        if step.get("requires_approval", False):
            current_task_status = "waiting_approval"
            await broadcast_event("status_change", {"status": current_task_status})
            await broadcast_event("approval_required", {
                "step_index": self.current_step_index,
                "title": step["title"],
                "description": f"AI muốn thực hiện: [{module}] {action} với tham số {params}"
            })
            
            await broadcast_event("log", {"level": "warning", "message": "Hành động yêu cầu phê duyệt bảo mật từ Người dùng. Đang chờ..."})
            
            approval_event.clear()
            await approval_event.wait() # Chờ client gửi tín hiệu qua WebSocket
            
            if approval_decision == "rejected":
                current_task_status = "running"
                await broadcast_event("status_change", {"status": current_task_status})
                return False, f"Người dùng đã từ chối bước này. Lý do: {approval_comment}"
                
            current_task_status = "running"
            await broadcast_event("status_change", {"status": current_task_status})
            await broadcast_event("log", {"level": "info", "message": "Người dùng đã chấp thuận. Đang tiếp tục..."})

        # --- Gọi Module Thích Hợp ---
        modules_mode = current_api_config.get("modules_mode", "local")
        remote_api_base = current_api_config.get("remote_api_base", "http://127.0.0.1:8000").rstrip('/')
        
        try:
            if module == "downloader":
                url = params.get("url")
                if not url:
                    return False, "Thiếu tham số 'url' cho downloader"
                
                if modules_mode == "remote":
                    await broadcast_event("log", {"level": "info", "message": f"[Remote API] Đang gửi yêu cầu tải video tới {remote_api_base}/api/modules/downloader/download..."})
                    async with httpx.AsyncClient(timeout=300.0) as client:
                        resp = await client.post(
                            f"{remote_api_base}/api/modules/downloader/download", 
                            json={"url": url, "quality": params.get("quality", "720p")}
                        )
                        resp.raise_for_status()
                        res = resp.json()
                else:
                    from backend.modules.downloader import VideoDownloader
                    async def progress_cb(info):
                        await broadcast_event("downloader_progress", info)
                    
                    dl = VideoDownloader(progress_callback=progress_cb)
                    await broadcast_event("log", {"level": "info", "message": f"Đang tải video từ url: {url}..."})
                    quality_param = params.get("quality", "best")
                    os.environ["DOWNLOAD_QUALITY"] = quality_param
                    res = await asyncio.to_thread(dl.download, url, quality=quality_param)
                
                if res.get("success"):
                    step["output_file"] = res["filepath"]
                    return True, f"Tải thành công: {res['title']}. File: {res['filepath']}"
                else:
                    return False, f"Lỗi khi tải video: {res.get('error')}"
                
            elif module == "subtitle":
                # Tìm file video từ đầu ra của bước trước hoặc truyền trực tiếp
                video_path = params.get("video_path")
                if video_path and not os.path.exists(video_path):
                    await broadcast_event("log", {"level": "warning", "message": f"video_path '{video_path}' không tồn tại, sẽ tự động tìm từ bước trước."})
                    video_path = None
                if not video_path:
                    for prev_step in self.plan_steps[:self.current_step_index]:
                        if "output_file" in prev_step:
                            video_path = prev_step["output_file"]
                            break
                            
                if not video_path:
                    return False, "Không tìm thấy đường dẫn video cho subtitle module"
                
                if action == "transcribe":
                    offset = float(current_api_config.get("subtitle_offset", 0.0))
                    bypass_enhance = params.get("bypass_enhance", False)
                    
                    if modules_mode == "remote":
                        if bypass_enhance:
                            await broadcast_event("log", {"level": "info", "message": "Bỏ qua trích xuất khử nhiễu theo sơ đồ Node-Graph."})
                            enhanced_audio = None
                            transcribe_target = video_path
                        else:
                            # 1. Trích xuất và khử nhiễu từ xa
                            await broadcast_event("log", {"level": "info", "message": f"[Remote API] Đang gửi yêu cầu làm sạch âm thanh tới {remote_api_base}/api/modules/audio/enhance..."})
                            async with httpx.AsyncClient(timeout=300.0) as client:
                                resp = await client.post(
                                    f"{remote_api_base}/api/modules/audio/enhance", 
                                    json={"video_path": str(video_path)}
                                )
                                resp.raise_for_status()
                                res_audio = resp.json()
                                enhanced_audio = res_audio.get("filepath")
                            
                            transcribe_target = enhanced_audio if enhanced_audio else video_path
                        
                        # 2. Nhận dạng giọng nói từ xa
                        await broadcast_event("log", {"level": "info", "message": f"[Remote API] Đang gửi yêu cầu nhận diện giọng nói tới {remote_api_base}/api/modules/subtitle/transcribe..."})
                        async with httpx.AsyncClient(timeout=600.0) as client:
                            resp = await client.post(
                                f"{remote_api_base}/api/modules/subtitle/transcribe", 
                                json={"media_path": str(transcribe_target), "offset": offset}
                            )
                            resp.raise_for_status()
                            res_sub = resp.json()
                            srt_content = res_sub.get("srt_content")
                            lang = res_sub.get("language")
                            self.detected_language = lang
                            
                        # Dọn dẹp tệp âm thanh tạm ở local nếu có
                        if enhanced_audio and os.path.exists(enhanced_audio):
                            try:
                                os.remove(enhanced_audio)
                            except Exception:
                                pass
                    else:
                        from backend.modules.subtitle import SubtitleEngine
                        from backend.modules.audio import AudioExtractor
                        
                        async def sub_cb(info):
                            await broadcast_event("log", {"level": "info", "message": f"[ASR] {info.get('message', '')}"})
                            
                        engine = SubtitleEngine(progress_callback=sub_cb)
                        audio_ext = AudioExtractor(progress_callback=lambda msg: broadcast_event("log", msg))
                        
                        if bypass_enhance:
                            await broadcast_event("log", {"level": "info", "message": "Bỏ qua trích xuất khử nhiễu theo sơ đồ Node-Graph."})
                            enhanced_audio = None
                            transcribe_target = video_path
                        else:
                            # 1. Trích xuất âm thanh
                            enhanced_audio = await audio_ext.enhance_audio(video_path)
                            transcribe_target = enhanced_audio if enhanced_audio else video_path
                        
                        # 2. Transcribe
                        srt_content, lang = await engine.transcribe(transcribe_target, offset=offset)
                        self.detected_language = lang
                        
                        # Dọn dẹp tệp âm thanh tạm
                        if enhanced_audio and os.path.exists(enhanced_audio):
                            try:
                                os.remove(enhanced_audio)
                            except Exception:
                                pass
                    
                    filename = os.path.basename(video_path)
                    base, _ = os.path.splitext(filename)
                    srt_path = os.path.join(os.path.dirname(video_path), f"{base}_{lang}.srt")
                    
                    with open(srt_path, 'w', encoding='utf-8') as f:
                        f.write(srt_content)
                    
                    # Phát sự kiện phụ đề sẵn sàng cho giao diện nếu không có bước dịch tiếp theo
                    has_translation = any(s.get("module") == "subtitle" and s.get("action") == "translate" for s in self.plan_steps[self.current_step_index+1:])
                    if not has_translation:
                        await broadcast_event("srt_ready", {"srt_content": srt_content, "is_translated": False})
                        
                    step["output_srt"] = srt_path
                    return True, f"Trích xuất phụ đề gốc ({lang}) thành công. SRT: {srt_path}"
                    
                elif action == "translate":
                    srt_path = params.get("srt_path")
                    if srt_path and not os.path.exists(srt_path):
                        srt_path = None
                    if not srt_path:
                        for prev_step in self.plan_steps[:self.current_step_index]:
                            if "output_srt" in prev_step:
                                srt_path = prev_step["output_srt"]
                                break
                                
                    if not srt_path:
                        return False, "Không tìm thấy file SRT để dịch"
                        
                    target_lang = params.get("target_lang", "Tiếng Việt")
                    
                    # Logic tự động bỏ qua dịch thuật nếu ngôn ngữ gốc trùng ngôn ngữ dịch mục tiêu
                    is_same_lang = False
                    detected_lang = getattr(self, "detected_language", "").lower()
                    target_lang_clean = target_lang.lower().strip()
                    
                    if detected_lang == "vi":
                        if "việt" in target_lang_clean or "viet" in target_lang_clean or target_lang_clean == "vi":
                            is_same_lang = True
                    elif detected_lang == "en":
                        if "eng" in target_lang_clean or target_lang_clean == "en":
                            is_same_lang = True
                    elif detected_lang and detected_lang == target_lang_clean:
                        is_same_lang = True
                        
                    if is_same_lang:
                        await broadcast_event("log", {"level": "warning", "message": f"[Translator] Phát hiện ngôn ngữ gốc của video trùng với ngôn ngữ mục tiêu ({target_lang}). Tự động bỏ qua bước dịch thuật để tối ưu tài nguyên!"})
                        base, ext = os.path.splitext(srt_path)
                        translated_srt_path = f"{base}_translated.srt"
                        import shutil
                        shutil.copy2(srt_path, translated_srt_path)
                        
                        with open(translated_srt_path, 'r', encoding='utf-8') as f:
                            translated_srt = f.read()
                        await broadcast_event("srt_ready", {"srt_content": translated_srt, "is_translated": True})
                        
                        step["output_srt_translated"] = translated_srt_path
                        return True, f"Bỏ qua bước dịch (ngôn ngữ gốc đã là {target_lang}). Bản sao phụ đề: {translated_srt_path}"

                    with open(srt_path, 'r', encoding='utf-8') as f:
                        srt_content = f.read()
                    
                    if modules_mode == "remote":
                        await broadcast_event("log", {"level": "info", "message": f"[Remote API] Đang gửi yêu cầu dịch thuật tới {remote_api_base}/api/modules/translator/translate..."})
                        async with httpx.AsyncClient(timeout=600.0) as client:
                            resp = await client.post(
                                f"{remote_api_base}/api/modules/translator/translate", 
                                json={"srt_content": srt_content, "target_lang": target_lang}
                            )
                            resp.raise_for_status()
                            res_trans = resp.json()
                            translated_srt = res_trans.get("srt_content")
                    else:
                        from backend.modules.translator import SubtitleTranslator
                        translator = SubtitleTranslator(progress_callback=lambda msg: broadcast_event("log", {"level": "info", "message": f"[Translator] {msg}"}))
                        translated_srt = await translator.translate_srt(srt_content, target_lang=target_lang)
                    
                    base, ext = os.path.splitext(srt_path)
                    translated_srt_path = f"{base}_translated.srt"
                    with open(translated_srt_path, 'w', encoding='utf-8') as f:
                        f.write(translated_srt)
                    
                    # Phát sự kiện phụ đề sẵn sàng cho giao diện
                    await broadcast_event("srt_ready", {"srt_content": translated_srt, "is_translated": True})
                        
                    step["output_srt_translated"] = translated_srt_path
                    return True, f"Dịch phụ đề thành công. File: {translated_srt_path}"
                    
                elif action == "burn":
                    srt_path = params.get("srt_path")
                    
                    if srt_path and not os.path.exists(srt_path):
                        srt_path = None
                    
                    if not srt_path:
                        for prev_step in reversed(self.plan_steps[:self.current_step_index]):
                            if "output_srt_translated" in prev_step:
                                srt_path = prev_step["output_srt_translated"]
                                break
                            elif "output_srt" in prev_step:
                                srt_path = prev_step["output_srt"]
                                break
                    
                    if not video_path or not srt_path:
                        return False, f"Thiếu file video ({video_path}) hoặc file srt ({srt_path}) để nhúng phụ đề"
                    
                    # NẾU người dùng đã sửa đổi srt qua editor, ghi đè nội dung mới vào srt_path trước khi nhúng!
                    global edited_srt_content
                    if edited_srt_content:
                        await broadcast_event("log", {"level": "info", "message": "Phát hiện phụ đề đã được người dùng chỉnh sửa từ giao diện. Đang ghi đè phụ đề mới..."})
                        with open(srt_path, 'w', encoding='utf-8') as f:
                            f.write(edited_srt_content)
                        # Xóa đi để tránh ảnh hưởng lần chạy sau
                        edited_srt_content = ""

                    # Kiểm tra xem có bật chế độ Dual Subtitle hay không
                    use_dual = current_api_config.get("dual_subtitles", False)
                    if use_dual:
                        # Tìm cả srt gốc và srt dịch
                        orig_srt_path = None
                        trans_srt_path = srt_path
                        
                        for prev_step in self.plan_steps[:self.current_step_index]:
                            if "output_srt" in prev_step:
                                orig_srt_path = prev_step["output_srt"]
                                break
                                
                        if orig_srt_path and trans_srt_path and os.path.exists(orig_srt_path) and os.path.exists(trans_srt_path):
                            with open(orig_srt_path, 'r', encoding='utf-8') as f:
                                orig_content = f.read()
                            with open(trans_srt_path, 'r', encoding='utf-8') as f:
                                trans_content = f.read()
                                
                            if modules_mode == "remote":
                                await broadcast_event("log", {"level": "info", "message": f"[Remote API] Đang gửi yêu cầu gộp song ngữ tới {remote_api_base}/api/modules/exporter/merge..."})
                                async with httpx.AsyncClient(timeout=120.0) as client:
                                    resp = await client.post(
                                        f"{remote_api_base}/api/modules/exporter/merge", 
                                        json={"orig_srt": orig_content, "trans_srt": trans_content}
                                    )
                                    resp.raise_for_status()
                                    res_merge = resp.json()
                                    dual_srt_content = res_merge.get("srt_content")
                            else:
                                from backend.modules.exporter import SubtitleExporter
                                exporter = SubtitleExporter(progress_callback=lambda msg: broadcast_event("log", {"level": "info", "message": f"[Exporter] {msg}"}))
                                dual_srt_content = exporter.merge_srt_dual(orig_content, trans_content)
                            
                            # Ghi đè vào một tệp srt kép tạm thời để burn
                            base, ext = os.path.splitext(trans_srt_path)
                            dual_srt_path = f"{base}_dual.srt"
                            with open(dual_srt_path, 'w', encoding='utf-8') as f:
                                f.write(dual_srt_content)
                            srt_path = dual_srt_path
                            await broadcast_event("log", {"level": "success", "message": "Gộp phụ đề song ngữ thành công!"})
                    
                    # Ưu tiên style phụ đề truyền từ tham số, sau đó là từ cấu hình tùy biến UI, cuối cùng là mặc định
                    subtitle_style = params.get("subtitle_style") or current_api_config.get("subtitle_style")
                    
                    if modules_mode == "remote":
                        await broadcast_event("log", {"level": "info", "message": f"[Remote API] Đang gửi yêu cầu nhúng phụ đề tới {remote_api_base}/api/modules/exporter/burn..."})
                        async with httpx.AsyncClient(timeout=300.0) as client:
                            resp = await client.post(
                                f"{remote_api_base}/api/modules/exporter/burn", 
                                json={"video_path": str(video_path), "srt_path": str(srt_path), "subtitle_style": subtitle_style}
                            )
                            resp.raise_for_status()
                            res = resp.json()
                    else:
                        from backend.modules.exporter import SubtitleExporter
                        exporter = SubtitleExporter(progress_callback=lambda msg: broadcast_event("log", {"level": "info", "message": f"[Exporter] {msg}"}))
                        res = await exporter.burn_subtitles(video_path, srt_path, subtitle_style=subtitle_style)
                        
                    if res.get("success"):
                        step["output_file"] = res["filepath"]
                        return True, f"Nhúng phụ đề thành công. Video cuối cùng: {res['filepath']}"
                    else:
                        return False, f"Lỗi nhúng phụ đề: {res.get('error')}"
                        
            elif module == "browser":

                from backend.modules.browser import BrowserAgent
                agent = BrowserAgent()
                url = params.get("url")
                task_desc = params.get("task")
                
                await broadcast_event("log", {"level": "info", "message": f"Khởi chạy Playwright để mở trang: {url}..."})
                success, result = await run_async_in_new_thread(agent.run_browser_task, url, task_desc)
                return success, result
                
            elif module == "voiceover":
                voice = params.get("voice", "vi-VN-HoaiMyNeural")
                mix_ratio = params.get("mix_ratio", 70)
                engine = params.get("engine", "edge")
                emotion = params.get("emotion", "neutral")
                api_key_openai = params.get("api_key_openai", "")
                api_key_elevenlabs = params.get("api_key_elevenlabs", "")
                
                # Tìm file video từ đầu ra của bước trước hoặc truyền trực tiếp
                voiceover_video_path = params.get("video_path")
                if voiceover_video_path and not os.path.exists(voiceover_video_path):
                    voiceover_video_path = None
                if not voiceover_video_path:
                    for prev_step in self.plan_steps[:self.current_step_index]:
                        if "output_file" in prev_step:
                            voiceover_video_path = prev_step["output_file"]
                            break
                
                # Tìm file srt từ các bước trước (ưu tiên file đã dịch mới nhất)
                srt_path = None
                for prev_step in reversed(self.plan_steps[:self.current_step_index]):
                    if "output_srt_translated" in prev_step:
                        srt_path = prev_step["output_srt_translated"]
                        break
                    elif "output_srt" in prev_step:
                        srt_path = prev_step["output_srt"]
                        break
                
                if not voiceover_video_path or not srt_path:
                    return False, f"Thiếu file video ({voiceover_video_path}) hoặc file srt ({srt_path}) để lồng tiếng AI"
                
                from backend.modules.voiceover import VoiceoverGenerator
                generator = VoiceoverGenerator(progress_callback=lambda msg: asyncio.create_task(broadcast_event("log", {"level": "info", "message": f"[Voiceover] {msg}"})))
                res = await generator.dub_video(
                    voiceover_video_path, srt_path,
                    voice=voice, mix_ratio=mix_ratio,
                    engine=engine, emotion=emotion,
                    api_key_openai=api_key_openai,
                    api_key_elevenlabs=api_key_elevenlabs
                )
                
                if res.get("success"):
                    step["output_file"] = res["filepath"]
                    return True, f"Lồng tiếng AI thành công. Video lồng tiếng: {res['filepath']}"
                else:
                    return False, f"Lỗi lồng tiếng AI: {res.get('error')}"

            elif module == "clipper":
                duration = params.get("duration", 60)
                aspect_ratio = params.get("aspect_ratio", "9:16")
                
                # Tìm file video từ các bước trước (ưu tiên file đã burn phụ đề hoặc lồng tiếng)
                input_video = None
                for prev_step in reversed(self.plan_steps[:self.current_step_index]):
                    if "output_file" in prev_step:
                        input_video = prev_step["output_file"]
                        break
                
                if not input_video or not os.path.exists(input_video):
                    return False, f"Không tìm thấy video đầu vào để trích xuất Shorts"
                
                from backend.modules.clipper import VideoClipper
                clipper = VideoClipper(progress_callback=lambda msg: broadcast_event("log", {"level": "info", "message": f"[Clipper] {msg}"}))
                res = await clipper.create_short(input_video, duration=duration, aspect_ratio=aspect_ratio)
                
                if res.get("success"):
                    step["output_file"] = res["filepath"]
                    return True, f"Trích xuất Shorts thành công ({aspect_ratio}). Video: {res['filepath']}"
                else:
                    return False, f"Lỗi trích xuất Shorts: {res.get('error')}"

            elif module == "seo":
                platform = params.get("platform", "youtube")
                tone = params.get("tone", "engaging")
                
                srt_path = None
                for prev_step in self.plan_steps[:self.current_step_index]:
                    if "output_srt_translated" in prev_step:
                        srt_path = prev_step["output_srt_translated"]
                        break
                    elif "output_srt" in prev_step:
                        srt_path = prev_step["output_srt"]
                
                if not srt_path or not os.path.exists(srt_path):
                    return False, "Không tìm thấy file phụ đề srt để phân tích SEO"
                
                with open(srt_path, 'r', encoding='utf-8') as f:
                    srt_text = f.read()
                
                await broadcast_event("log", {"level": "info", "message": f"Đang dùng LLM sinh mô tả SEO quảng bá lên {platform} (Tone: {tone})..."})
                seo_prompt = (
                    f"Hãy đóng vai là một nhà sáng tạo nội dung (Content Creator) chuyên nghiệp hàng đầu tại Việt Nam trên nền tảng {platform}.\n"
                    f"Phân tích tệp phụ đề video sau và viết một bài mô tả đăng video cực kỳ tự nhiên, cuốn hút và chuẩn SEO.\n"
                    f"Giọng điệu thể hiện: {tone} (tự nhiên, không gượng ép).\n\n"
                    "YÊU CẦU QUAN TRỌNG VỀ VĂN PHONG (ĐỂ ĐẢM BẢO SỰ TỰ NHIÊN NHƯ NGƯỜI THẬT VIẾT):\n"
                    "- TUYỆT ĐỐI TRÁNH giọng điệu rập khuôn, sáo rỗng của AI (tránh các từ như: 'Chào mừng các bạn đã quay trở lại...', 'Trong video ngày hôm nay chúng ta sẽ...', 'vô cùng', 'cực kỳ', 'hãy cùng tôi khám phá...').\n"
                    "- Viết thẳng vào vấn đề bằng một câu hook (mở đầu) gây tò mò, đánh trúng nỗi đau hoặc kích thích người dùng.\n"
                    "- Sử dụng ngôn ngữ giao tiếp đời thường nhưng văn minh, gần gũi (dùng đại từ xưng hô tự nhiên như 'mình', 'bạn', 'cả nhà').\n"
                    "- Hạn chế tối đa việc lạm dụng dấu chấm than (!) và emoji. Chỉ chèn emoji thật tinh tế ở cuối câu để tạo điểm nhấn, không chèn tràn lan ở đầu mỗi dòng.\n"
                    "- Tập trung chia sẻ giá trị cốt lõi của video theo dạng kể chuyện (storytelling) ngắn gọn.\n\n"
                    "BỐ CỤC BÀI VIẾT BẮT BUỘC (PHẢI ĐÚNG CÁC TỪ KHÓA BÊN DƯỚI ĐỂ HỆ THỐNG TỰ ĐỘNG PARSE THÔNG TIN):\n"
                    "TIÊU ĐỀ: [Viết 1 tiêu đề video tiếng Việt giật gân, cực kỳ cuốn hút, chuẩn tỷ lệ click CTR, dài dưới 80 ký tự]\n"
                    "MÔ TẢ:\n"
                    f"[Đoạn mô tả ngắn 2-3 câu tự nhiên kể chuyện để hút người xem trên {platform}...]\n"
                    "[Tóm tắt súc tích 3-4 ý chính nổi bật nhất của video...]\n"
                    "CHƯƠNG MỤC:\n"
                    "[Lập bảng mốc thời gian kiểu YouTube, ví dụ: 00:00 - Tiêu đề chương. Bắt buộc phải có mốc 00:00]\n"
                    "HASHTAGS:\n"
                    "[3-5 hashtags xu hướng, liên quan trực tiếp và tự nhiên nhất]\n\n"
                    "LƯU Ý: Trả về định dạng văn bản trực tiếp sạch sẽ (không bọc trong khối ```code```).\n\n"
                    f"Nội dung phụ đề:\n{srt_text[:3000]}"
                )
                try:
                    seo_result = await call_llm(seo_prompt, system_prompt="You are an expert AI social media marketer and SEO optimizer.")
                    step["output_seo"] = seo_result
                    await broadcast_event("seo_ready", {"seo_content": seo_result})
                    return True, "Đã sinh nội dung bài viết tiếp thị SEO thành công!"
                except Exception as seo_err:
                    return False, f"Lỗi khi gọi LLM tạo SEO: {str(seo_err)}"

            elif module == "uploader":
                # Tìm video thành phẩm cuối cùng để tải lên
                final_video_path = None
                for prev_step in reversed(self.plan_steps[:self.current_step_index]):
                    if "output_file" in prev_step:
                        final_video_path = prev_step["output_file"]
                        break
                
                if not final_video_path or not os.path.exists(final_video_path):
                    return False, "Không tìm thấy file video thành phẩm để xuất bản"
                
                from backend.modules.uploader import upload_to_google_drive, send_to_telegram, upload_to_youtube
                gdrive = params.get("gdrive", False)
                telegram = params.get("telegram", False)
                youtube = params.get("youtube", False)
                privacy = params.get("privacy_status", "private")
                
                if gdrive:
                    await upload_to_google_drive(final_video_path, progress_callback=lambda msg: broadcast_event("log", msg))
                if telegram:
                    telegram_caption = f"🎬 <b>{os.path.basename(final_video_path)}</b> đã được xuất bản tự động qua Aegis Workflow!"
                    await send_to_telegram(final_video_path, telegram_caption, progress_callback=lambda msg: broadcast_event("log", msg))
                if youtube:
                    # Lấy bài viết SEO AI làm mô tả YouTube
                    seo_content = ""
                    for prev_step in self.plan_steps[:self.current_step_index]:
                        if "output_seo" in prev_step:
                            seo_content = prev_step["output_seo"]
                            break
                    if not seo_content:
                        seo_content = "Video được xuất bản tự động qua Aegis Workflow."
                        
                    # Tự động trích xuất tiêu đề cuốn hút do AI tạo từ phần nội dung SEO
                    video_title = os.path.splitext(os.path.basename(final_video_path))[0]
                    if seo_content:
                        lines = seo_content.split('\n')
                        parsed_title = None
                        for idx_line, line in enumerate(lines):
                            match_kw = re.search(r'(?:TIÊU\s*ĐỀ|Tiêu\s*đề|TITLE|Title)\b', line, re.IGNORECASE)
                            if match_kw:
                                content_after = line[match_kw.end():].strip()
                                content_after = re.sub(r'^[:\s\*\-\[\]“"”\'«»]+', '', content_after)
                                content_after = re.sub(r'[:\s\*\-\[\]“"”\'«»]+$', '', content_after)
                                
                                sec_match = re.search(r'\s*(?:\*\*|)?(?:MÔ\s*TẢ|CHƯƠNG\s*MỤC|HASHTAGS|Description|Chapters|Tags)\b', content_after, re.IGNORECASE)
                                if sec_match:
                                    content_after = content_after[:sec_match.start()].strip()
                                    content_after = re.sub(r'^[:\s\*\-\[\]“"”\'«»]+', '', content_after)
                                    content_after = re.sub(r'[:\s\*\-\[\]“"”\'«»]+$', '', content_after)
                                
                                if len(content_after) > 3:
                                    parsed_title = content_after
                                    break
                                
                                lookahead = idx_line + 1
                                while lookahead < len(lines):
                                    next_line = lines[lookahead].strip()
                                    next_cleaned = re.sub(r'^[:\s\*\-\[\]“"”\'«»]+', '', next_line)
                                    next_cleaned = re.sub(r'[:\s\*\-\[\]“"”\'«»]+$', '', next_cleaned)
                                    if re.search(r'^(?:MÔ\s*TẢ|CHƯƠNG\s*MỤC|HASHTAGS|Description|Chapters|Tags)\b', next_cleaned, re.IGNORECASE):
                                        break
                                    if len(next_cleaned) > 3:
                                        parsed_title = next_cleaned
                                        break
                                    lookahead += 1
                                if parsed_title:
                                    break
                        
                        if parsed_title:
                            video_title = parsed_title
                        else:
                            match = re.search(r'(?:TIÊU ĐỀ|Tiêu đề|TITLE|Title)\s*:\s*(.+)', seo_content)
                            if match:
                                video_title = match.group(1).strip().replace('[', '').replace(']', '').replace('**', '').strip(' "“’\'')
                            
                    approved, final_title, final_desc = await self.review_and_approve_seo(video_title, seo_content)
                    if not approved:
                        return False, "Người dùng đã từ chối phê duyệt nội dung SEO & Tiêu đề đăng YouTube."
                        
                    await upload_to_youtube(
                        final_video_path, 
                        final_title, 
                        final_desc, 
                        privacy_status=privacy,
                        progress_callback=lambda msg: broadcast_event("log", msg)
                    )
                
                return True, "Đã xuất bản và sao lưu đám mây thành công!"
                
            else:
                return False, f"Không hỗ trợ mô-đun '{module}'"
                
        except Exception as e:
            traceback.print_exc()
            return False, f"Lỗi xảy ra tại module {module}: {str(e)}"

    async def execute_workflow(self, goal: str, nodes_data: dict, connections_data: list, 
                               subtitle_style: str = "", upload_gdrive: bool = False, upload_telegram: bool = False, upload_youtube: bool = False):
        """
        Khởi chạy thực hiện Sơ đồ Node-Graph Workflow động.
        """
        global current_task_status, current_api_config
        
        is_queued = execution_lock.locked()
        if is_queued:
            await broadcast_event("log", {"level": "warning", "message": f"[Hàng đợi] Hệ thống đang bận. Đã xếp hàng Workflow cho mục tiêu: '{goal}'..."})
            await broadcast_event("status_change", {"status": "queued"})
            
        async with execution_lock:
            # 1. Giải mã và nạp cấu hình động của từng Node vào current_api_config
            trans_cfg = nodes_data.get("translator", {}).get("config", {})
            sub_cfg = nodes_data.get("subtitle", {}).get("config", {})
            exp_cfg = nodes_data.get("exporter", {}).get("config", {})
            audio_cfg = nodes_data.get("audio", {}).get("config", {})
            dl_cfg = nodes_data.get("downloader", {}).get("config", {})
            seo_cfg = nodes_data.get("seo", {}).get("config", {})
            voice_cfg = nodes_data.get("voiceover", {}).get("config", {})
            clip_cfg = nodes_data.get("clipper", {}).get("config", {})
            pub_cfg = nodes_data.get("uploader", {}).get("config", {})
            
            # Cập nhật API config cho Translator
            current_api_config["provider"] = trans_cfg.get("provider", "ollama")
            current_api_config["api_base"] = trans_cfg.get("api_base", "http://localhost:11434")
            current_api_config["api_key"] = trans_cfg.get("api_key", "")
            current_api_config["model"] = trans_cfg.get("model", "deepseek-r1:8b")
            
            # Cập nhật Exporter style và Dual Sub
            current_api_config["subtitle_style"] = subtitle_style
            current_api_config["dual_subtitles"] = exp_cfg.get("dual_subtitles", False)
            current_api_config["subtitle_offset"] = float(sub_cfg.get("offset", 0.0))
            
            # Cập nhật Cloud upload
            current_api_config["upload_gdrive"] = upload_gdrive or pub_cfg.get("gdrive", False)
            current_api_config["upload_telegram"] = upload_telegram or pub_cfg.get("telegram", False)
            current_api_config["upload_youtube"] = upload_youtube or pub_cfg.get("youtube", False)
            current_api_config["privacy_status"] = pub_cfg.get("privacy_status", "private")
            
            self.current_goal = goal
            self.current_step_index = 0
            self.active_nodes_data = nodes_data
            
            # Đồng bộ hoá cấu hình Node thực tế đang chạy lên WebUI
            await broadcast_event("sync_nodes", {"nodes_data": nodes_data})
            
            current_task_status = "planning"
            await broadcast_event("status_change", {"status": current_task_status})
            await broadcast_event("log", {"level": "info", "message": f"Bắt đầu thực hiện Workflow: '{goal}'"})
            
            try:
                # 2. Xây dựng Workflow tuần tự dựa trên các Node được BẬT (không bypassed)
                self.plan_steps = []
                
                # Node 1: Downloader
                if not nodes_data.get("downloader", {}).get("bypassed", False):
                    # Tự động lấy URL từ Goal
                    url_match = re.search(r'(https?://[^\s]+)', goal)
                    url = url_match.group(1) if url_match else "https://www.youtube.com/watch?v=DujM57DP4u4"
                    self.plan_steps.append({
                        "title": f"Tải video chất lượng {dl_cfg.get('quality', 'best')} từ URL",
                        "module": "downloader",
                        "action": "download",
                        "params": {"url": url, "quality": dl_cfg.get("quality", "best")},
                        "status": "pending"
                    })
                
                # Check xem node audio có bị bypass hay không để gửi thông tin cho Subtitle Node
                audio_bypassed = nodes_data.get("audio", {}).get("bypassed", False)
                
                # Node 3: Subtitle ASR (transcribe)
                if not nodes_data.get("subtitle", {}).get("bypassed", False):
                    self.plan_steps.append({
                        "title": "Nhận diện giọng nói và tạo phụ đề gốc (Whisper)" + (" (Bỏ qua khử nhiễu)" if audio_bypassed else " (AI Enhanced Audio)"),
                        "module": "subtitle",
                        "action": "transcribe",
                        "params": {
                            "model": sub_cfg.get("model", "large-v3"), 
                            "device": sub_cfg.get("device", "cuda"),
                            "bypass_enhance": audio_bypassed # truyền trạng thái bypass của audio node!
                        },
                        "status": "pending"
                    })
                
                # Node 4: Translator
                if not nodes_data.get("translator", {}).get("bypassed", False):
                    self.plan_steps.append({
                        "title": f"Dịch phụ đề song song sang {trans_cfg.get('target_lang', 'Tiếng Việt')}",
                        "module": "subtitle",
                        "action": "translate",
                        "params": {"target_lang": trans_cfg.get("target_lang", "Tiếng Việt")},
                        "status": "pending"
                    })

                # Node 5: Voiceover TTS (MỚI!)
                if not nodes_data.get("voiceover", {}).get("bypassed", False):
                    self.plan_steps.append({
                        "title": f"Lồng tiếng AI giọng {voice_cfg.get('voice', 'vi-VN-HoaiAnNeural')}",
                        "module": "voiceover",
                        "action": "tts",
                        "params": {
                            "voice": voice_cfg.get("voice", "vi-VN-HoaiAnNeural"),
                            "mix_ratio": float(voice_cfg.get("mix_ratio", 70)) / 100.0
                        },
                        "status": "pending"
                    })
                
                # Node 6: Exporter Burn
                if not nodes_data.get("exporter", {}).get("bypassed", False):
                    self.plan_steps.append({
                        "title": "Nhúng phụ đề ASS cứng vào video bằng FFmpeg",
                        "module": "subtitle",
                        "action": "burn",
                        "params": {"subtitle_style": subtitle_style},
                        "status": "pending"
                    })
                
                # Node 7: SEO & Marketing (MỚI!)
                if not nodes_data.get("seo", {}).get("bypassed", False):
                    self.plan_steps.append({
                        "title": f"Sinh nội dung SEO bài đăng {seo_cfg.get('platform', 'youtube').upper()} bằng LLM AI",
                        "module": "seo",
                        "action": "generate",
                        "params": {
                            "platform": seo_cfg.get("platform", "youtube"),
                            "tone": seo_cfg.get("tone", "engaging")
                        },
                        "status": "pending"
                    })
                
                # Node 8: Shorts Clipper (MỚI!)
                if not nodes_data.get("clipper", {}).get("bypassed", False):
                    self.plan_steps.append({
                        "title": f"Cắt Shorts {clip_cfg.get('aspect_ratio', '9:16')} highlight {clip_cfg.get('duration', 60)}s bằng FFmpeg",
                        "module": "clipper",
                        "action": "clip",
                        "params": {
                            "duration": int(clip_cfg.get("duration", 60)),
                            "aspect_ratio": clip_cfg.get("aspect_ratio", "9:16")
                        },
                        "status": "pending"
                    })
                
                # Node 9: Cloud Publisher (MỚI!)
                if not nodes_data.get("uploader", {}).get("bypassed", False):
                    if pub_cfg.get("gdrive", False) or pub_cfg.get("telegram", False) or pub_cfg.get("youtube", False):
                        self.plan_steps.append({
                            "title": "Đăng tải và sao lưu thành phẩm lên đám mây",
                            "module": "uploader",
                            "action": "publish",
                            "params": {
                                "gdrive": pub_cfg.get("gdrive", False),
                                "telegram": pub_cfg.get("telegram", False),
                                "youtube": pub_cfg.get("youtube", False),
                                "privacy_status": pub_cfg.get("privacy_status", "private")
                            },
                            "status": "pending"
                        })
                
                # Gửi Roadmap ban đầu cho UI
                await broadcast_event("plan_created", {"steps": self.plan_steps})
                
                if not self.plan_steps:
                    current_task_status = "completed"
                    await broadcast_event("status_change", {"status": current_task_status})
                    await broadcast_event("log", {"level": "warning", "message": "Workflow không có nút nào hoạt động. Hoàn thành sớm!"})
                    return
                
                # Khởi tạo lại edited_srt_content nếu có bước tạo phụ đề hoặc dịch thuật mới
                global edited_srt_content
                if any(s.get("action") in ("transcribe", "translate") for s in self.plan_steps):
                    edited_srt_content = ""
                
                current_task_status = "running"
                await broadcast_event("status_change", {"status": current_task_status})
                
                # 3. Chạy từng bước workflow tuần tự
                for idx, step in enumerate(self.plan_steps):
                    self.current_step_index = idx
                    await broadcast_event("step_start", {"index": idx})
                    await broadcast_event("log", {"level": "info", "message": f"Đang chạy Bước {idx+1}: {step['title']}..."})
                    
                    success, result_message = await self.execute_step(step)
                
                # Hoàn thành tất cả các bước thành công!
                current_task_status = "completed"
                await broadcast_event("status_change", {"status": current_task_status})
                await broadcast_event("log", {"level": "success", "message": "Chúc mừng! Hệ thống đã hoàn thành xuất sắc Workflow!"})
                
                # Tóm tắt SEO AI nếu có phụ đề
                await self.generate_seo_post_execution()
                
                # Upload tự động
                await self.uploader_post_execution()
                
            except Exception as e:
                traceback.print_exc()
                current_task_status = "failed"
                await broadcast_event("status_change", {"status": current_task_status})
                await broadcast_event("log", {"level": "error", "message": f"Lỗi hệ thống Workflow: {str(e)}"})

    async def review_and_approve_seo(self, default_title: str, default_seo: str):
        """
        Kích hoạt màn hình duyệt/sửa đổi Tiêu đề và Mô tả SEO trước khi đăng tải lên YouTube.
        """
        global current_task_status, approval_event, approval_decision, approval_comment, approved_seo_title, approved_seo_desc
        
        current_task_status = "waiting_approval"
        await broadcast_event("status_change", {"status": current_task_status})
        
        # Reset các biến duyệt trước đó
        approved_seo_title = ""
        approved_seo_desc = ""
        approval_decision = None
        
        # Tìm video kết quả cuối cùng từ các bước đã chạy
        final_video_path = None
        for step in reversed(self.plan_steps):
            if "output_file" in step:
                final_video_path = step["output_file"]
                break
                
        # Broadcast sự kiện yêu cầu phê duyệt đặc biệt có kèm SEO, Tiêu đề & Video thành phẩm
        await broadcast_event("approval_required", {
            "step_index": self.current_step_index,
            "title": "Kiểm Duyệt Nội Dung SEO & Tiêu Đề YouTube",
            "description": "Vui lòng xem lại và chỉnh sửa (nếu cần) tiêu đề cùng mô tả SEO do AI Employee tạo ra dưới đây trước khi đăng lên YouTube.",
            "is_seo_review": True,
            "seo_title": default_title,
            "seo_desc": default_seo,
            "video_path": final_video_path
        })
        
        await broadcast_event("log", {"level": "warning", "message": "🔔 Hệ thống đang chờ bạn phê duyệt và tối ưu tiêu đề/mô tả SEO trước khi tải lên YouTube..."})
        
        approval_event.clear()
        await approval_event.wait()
        
        current_task_status = "running"
        await broadcast_event("status_change", {"status": current_task_status})
        
        if approval_decision == "rejected":
            await broadcast_event("log", {"level": "error", "message": f"❌ Đã từ chối đăng tải video lên YouTube. Lý do: {approval_comment}"})
            return False, None, None
            
        # Lấy tiêu đề và mô tả đã được duyệt/chỉnh sửa từ client gửi lên
        final_title = approved_seo_title if approved_seo_title else default_title
        final_desc = approved_seo_desc if approved_seo_desc else default_seo
        
        return True, final_title, final_desc

    async def generate_seo_post_execution(self):
        """Tự động sinh mô tả SEO, Hashtags và chương mục từ file srt kết quả"""
        # Không chạy nếu node SEO bị bypass trong cấu hình
        if hasattr(self, 'active_nodes_data') and self.active_nodes_data and self.active_nodes_data.get("seo", {}).get("bypassed", False):
            return
        # Nếu bước sinh SEO đã được thực hiện như một phần của plan_steps, không chạy lại nữa để tránh trùng lặp
        if any(step.get("module") == "seo" for step in self.plan_steps):
            return
        translated_srt_path = None
        for step in reversed(self.plan_steps):
            if step.get("module") == "subtitle" and step.get("action") == "translate" and "output_srt_translated" in step:
                translated_srt_path = step["output_srt_translated"]
                break
            elif step.get("module") == "subtitle" and step.get("action") == "transcribe" and "output_srt" in step:
                translated_srt_path = step["output_srt"]
                break
                
        if translated_srt_path and os.path.exists(translated_srt_path):
            with open(translated_srt_path, 'r', encoding='utf-8') as f:
                srt_text = f.read()
            
            await broadcast_event("log", {"level": "info", "message": "Đang dùng LLM sinh mô tả SEO, Hashtag xu hướng và chương mục (Chapters)..."})
            seo_prompt = (
                "Hãy đóng vai là một nhà sáng tạo nội dung (Content Creator) chuyên nghiệp hàng đầu tại Việt Nam.\n"
                "Phân tích tệp phụ đề video sau và viết một bài mô tả đăng video cực kỳ tự nhiên, cuốn hút và chuẩn SEO.\n\n"
                "YÊU CẦU QUAN TRỌNG VỀ VĂN PHONG (ĐỂ ĐẢM BẢO SỰ TỰ NHIÊN NHƯ NGƯỜI THẬT VIẾT):\n"
                "- TUYỆT ĐỐI TRÁNH giọng điệu rập khuôn, sáo rỗng của AI (tránh các từ như: 'Chào mừng các bạn đã quay trở lại...', 'Trong video ngày hôm nay chúng ta sẽ...', 'vô cùng', 'cực kỳ', 'hãy cùng tôi khám phá...').\n"
                "- Viết thẳng vào vấn đề bằng một câu hook (mở đầu) gây tò mò, đánh trúng nỗi đau hoặc kích thích người dùng.\n"
                "- Sử dụng ngôn ngữ giao tiếp đời thường nhưng văn minh, gần gũi (dùng đại từ xưng hô tự nhiên như 'mình', 'bạn', 'cả nhà').\n"
                "- Hạn chế tối đa việc lạm dụng dấu chấm than (!) và emoji. Chỉ chèn emoji thật tinh tế ở cuối câu để tạo điểm nhấn, không chèn tràn lan ở đầu mỗi dòng.\n"
                "- Tập trung chia sẻ giá trị cốt lõi của video theo dạng kể chuyện (storytelling) ngắn gọn.\n\n"
                "BỐ CỤC BÀI VIẾT BẮT BUỘC (PHẢI ĐÚNG CÁC TỪ KHÓA BÊN DƯỚI ĐỂ HỆ THỐNG TỰ ĐỘNG PARSE THÔNG TIN):\n"
                "TIÊU ĐỀ: [Viết 1 tiêu đề video tiếng Việt giật gân, cực kỳ cuốn hút, chuẩn tỷ lệ click CTR, dài dưới 80 ký tự]\n"
                "MÔ TẢ:\n"
                "[Đoạn mô tả ngắn 2-3 câu tự nhiên kể chuyện để hút người xem nhấn nút 'Xem thêm'...]\n"
                "[Tóm tắt súc tích 3-4 ý chính nổi bật nhất của video...]\n"
                "CHƯƠNG MỤC:\n"
                "[Lập bảng mốc thời gian kiểu YouTube, ví dụ: 00:00 - Tiêu đề chương. Bắt buộc phải có mốc 00:00]\n"
                "HASHTAGS:\n"
                "[3-5 hashtags xu hướng, liên quan trực tiếp và tự nhiên nhất]\n\n"
                "LƯU Ý: Trả về định dạng văn bản trực tiếp sạch sẽ (không bọc trong khối ```code```).\n\n"
                f"Nội dung phụ đề:\n{srt_text[:3000]}"
            )
            try:
                seo_result = await call_llm(seo_prompt, system_prompt="You are an expert AI social media marketer and SEO optimizer.")
                await broadcast_event("log", {"level": "success", "message": "Đã sinh nội dung SEO và chương mục thành công!"})
                await broadcast_event("seo_ready", {"seo_content": seo_result})
            except Exception as seo_err:
                await broadcast_event("log", {"level": "warning", "message": f"Không thể tự động sinh SEO: {str(seo_err)}"})

    async def uploader_post_execution(self):
        """Tự động đăng tải lên Google Drive / Telegram Bot / YouTube nếu được kích hoạt"""
        # Không chạy nếu node uploader bị bypass trong cấu hình
        if hasattr(self, 'active_nodes_data') and self.active_nodes_data and self.active_nodes_data.get("uploader", {}).get("bypassed", False):
            return
        # Nếu bước uploader đã được thực hiện như một phần của plan_steps, không chạy uploader_post_execution nữa để tránh đăng 2 lần
        if any(step.get("module") == "uploader" for step in self.plan_steps):
            return
        final_video_path = None
        for step in reversed(self.plan_steps):
            if "output_file" in step:
                final_video_path = step["output_file"]
                break
                
        if final_video_path and os.path.exists(final_video_path):
            from backend.modules.uploader import upload_to_google_drive, send_to_telegram, upload_to_youtube
            
            if current_api_config.get("upload_gdrive", False):
                await upload_to_google_drive(final_video_path, progress_callback=lambda msg: broadcast_event("log", msg))
                
            if current_api_config.get("upload_telegram", False):
                telegram_caption = f"🎬 <b>{os.path.basename(final_video_path)}</b> đã hoàn thành qua Workflow tự động hóa!\n\n"
                await send_to_telegram(final_video_path, telegram_caption, progress_callback=lambda msg: broadcast_event("log", msg))

            if current_api_config.get("upload_youtube", False):
                # Lấy bài viết SEO AI làm mô tả YouTube
                seo_content = ""
                for step in reversed(self.plan_steps):
                    if "output_seo" in step:
                        seo_content = step["output_seo"]
                        break
                if not seo_content:
                    seo_content = "Video được xuất bản tự động qua Aegis Workflow."
                    
                # Tự động trích xuất tiêu đề cuốn hút do AI tạo từ phần nội dung SEO
                video_title = os.path.splitext(os.path.basename(final_video_path))[0]
                if seo_content:
                    lines = seo_content.split('\n')
                    parsed_title = None
                    for idx_line, line in enumerate(lines):
                        match_kw = re.search(r'(?:TIÊU\s*ĐỀ|Tiêu\s*đề|TITLE|Title)\b', line, re.IGNORECASE)
                        if match_kw:
                            content_after = line[match_kw.end():].strip()
                            content_after = re.sub(r'^[:\s\*\-\[\]“"”\'«»]+', '', content_after)
                            content_after = re.sub(r'[:\s\*\-\[\]“"”\'«»]+$', '', content_after)
                            
                            sec_match = re.search(r'\s*(?:\*\*|)?(?:MÔ\s*TẢ|CHƯƠNG\s*MỤC|HASHTAGS|Description|Chapters|Tags)\b', content_after, re.IGNORECASE)
                            if sec_match:
                                content_after = content_after[:sec_match.start()].strip()
                                content_after = re.sub(r'^[:\s\*\-\[\]“"”\'«»]+', '', content_after)
                                content_after = re.sub(r'[:\s\*\-\[\]“"”\'«»]+$', '', content_after)
                            
                            if len(content_after) > 3:
                                parsed_title = content_after
                                break
                            
                            lookahead = idx_line + 1
                            while lookahead < len(lines):
                                next_line = lines[lookahead].strip()
                                next_cleaned = re.sub(r'^[:\s\*\-\[\]“"”\'«»]+', '', next_line)
                                next_cleaned = re.sub(r'[:\s\*\-\[\]“"”\'«»]+$', '', next_cleaned)
                                if re.search(r'^(?:MÔ\s*TẢ|CHƯƠNG\s*MỤC|HASHTAGS|Description|Chapters|Tags)\b', next_cleaned, re.IGNORECASE):
                                    break
                                if len(next_cleaned) > 3:
                                    parsed_title = next_cleaned
                                    break
                                lookahead += 1
                            if parsed_title:
                                break
                    
                    if parsed_title:
                        video_title = parsed_title
                    else:
                        match = re.search(r'(?:TIÊU ĐỀ|Tiêu đề|TITLE|Title)\s*:\s*(.+)', seo_content)
                        if match:
                            video_title = match.group(1).strip().replace('[', '').replace(']', '').replace('**', '').strip(' "“’\'')
                        
                approved, final_title, final_desc = await self.review_and_approve_seo(video_title, seo_content)
                if approved:
                    privacy_status = current_api_config.get("privacy_status", "private")
                    await upload_to_youtube(
                        final_video_path, 
                        final_title, 
                        final_desc, 
                        privacy_status=privacy_status,
                        progress_callback=lambda msg: broadcast_event("log", msg)
                    )

