import os
import re
import json
import sys
import asyncio
import traceback

# Đảm bảo stdout và stderr sử dụng UTF-8 trên Windows để tránh lỗi UnicodeEncodeError khi in Emojis / Tiếng Việt
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding='utf-8')
from pathlib import Path
import httpx
from dotenv import load_dotenv

# Đảm bảo các biến môi trường được tải
load_dotenv(Path(__file__).resolve().parent / ".env")

# Cấu hình Token Telegram Bot từ biến môi trường
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# State variables toàn cục của Telegram Bot Session
active_chat_id = None
status_message_id = None
review_message_id = None
plan_steps = []
current_step_index = 0
workflow_running = False
goal_url = ""

# Lưu trữ Tiêu đề và Mô tả đang trong giai đoạn Review để người dùng cập nhật nóng
pending_seo_title = ""
pending_seo_desc = ""

# Regex tìm kiếm liên kết (URL) chung
URL_PATTERN = re.compile(
    r'(https?://[^\s]+)',
    re.IGNORECASE
)

# Helper gọi API Telegram Bot an toàn
async def call_telegram_api(method: str, payload: dict) -> dict:
    if not BOT_TOKEN:
        return {}
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        print(f"[TelegramBot] Lỗi khi gọi API Telegram {method}: {str(e)}")
        return {}

async def send_message(chat_id: int, text: str, reply_markup: dict = None) -> dict:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    res = await call_telegram_api("sendMessage", payload)
    return res.get("result", {})

async def edit_message(chat_id: int, message_id: int, text: str, reply_markup: dict = None) -> dict:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    res = await call_telegram_api("editMessageText", payload)
    return res.get("result", {})

async def send_video(chat_id: int, video_path: str, caption: str = None) -> dict:
    if not BOT_TOKEN:
        return {}
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"
    video_path = Path(video_path).resolve()
    if not video_path.exists():
        print(f"[TelegramBot] File video không tồn tại để gửi: {video_path}")
        return {}
        
    print(f"[TelegramBot] Đang tải lên và gửi video qua Telegram chat...")
    try:
        # Sử dụng multipart upload
        async with httpx.AsyncClient(timeout=300.0) as client:
            with open(video_path, "rb") as video_file:
                files = {"video": (video_path.name, video_file, "video/mp4")}
                data = {
                    "chat_id": chat_id,
                    "parse_mode": "HTML"
                }
                if caption:
                    data["caption"] = caption[:1024]
                
                resp = await client.post(url, data=data, files=files)
                resp.raise_for_status()
                return resp.json()
    except Exception as e:
        print(f"[TelegramBot] Lỗi tải lên video lên Telegram: {str(e)}")
        return {}

# Hàm định dạng tiến trình Workflow sang Markdown HTML tuyệt đẹp
def format_status_message() -> str:
    global plan_steps, current_step_index, goal_url
    
    msg = f"⚡ <b>TIẾN TRÌNH AEGIS WORKFLOW ĐANG CHẠY</b>\n"
    msg += f"🔗 <b>Link gốc:</b> <code>{goal_url}</code>\n\n"
    
    for idx, step in enumerate(plan_steps):
        status = step.get("status", "pending")
        title = step.get("title", "Chưa xác định")
        
        # Chọn icon trực quan theo trạng thái
        if status == "completed":
            icon = "✅"
        elif status == "failed":
            icon = "❌"
        elif status == "running":
            icon = "⚡"
        else:
            icon = "⏳"
            
        msg += f"{icon} <b>Bước {idx+1}:</b> {title}\n"
        
    msg += f"\n<i>Hệ thống xử lý song song và tăng tốc phần cứng GPU CUDA...</i>"
    return msg

# --- ĐĂNG KÝ HÀM SUBSCRIPTION EVENT BUS CHÍNH THỨC ---
async def telegram_event_handler(event_type: str, data: dict):
    global active_chat_id, status_message_id, review_message_id, plan_steps, current_step_index, workflow_running
    global pending_seo_title, pending_seo_desc
    
    if not active_chat_id:
        return
        
    try:
        # 1. Khi sơ đồ workflow bắt đầu được khởi tạo
        if event_type == "plan_created":
            plan_steps = data.get("steps", [])
            for step in plan_steps:
                step["status"] = "pending"
                
            text = format_status_message()
            res = await send_message(active_chat_id, text)
            if res:
                status_message_id = res.get("message_id")
                
        # 2. Khi một bước bắt đầu chạy
        elif event_type == "step_start":
            idx = data.get("index", 0)
            current_step_index = idx
            if idx < len(plan_steps):
                plan_steps[idx]["status"] = "running"
                if status_message_id:
                    await edit_message(active_chat_id, status_message_id, format_status_message())
                    
        # 3. Khi một bước hoàn thành
        elif event_type == "step_complete":
            idx = data.get("index", 0)
            result = data.get("result", "")
            if idx < len(plan_steps):
                plan_steps[idx]["status"] = "completed"
                if status_message_id:
                    await edit_message(active_chat_id, status_message_id, format_status_message())
                    
        # 4. Khi một bước thất bại
        elif event_type == "step_fail":
            idx = data.get("index", 0)
            error = data.get("error", "")
            if idx < len(plan_steps):
                plan_steps[idx]["status"] = "failed"
                if status_message_id:
                    await edit_message(active_chat_id, status_message_id, format_status_message())
                    
            workflow_running = False
            await send_message(active_chat_id, f"❌ <b>Thất bại ở Bước {idx+1}:</b> {error}\n\n<i>Quy trình tự động đã dừng lại. Vui lòng kiểm tra lại.</i>")
            
        # 5. Khi hệ thống yêu cầu duyệt SEO và Tiêu đề
        elif event_type == "approval_required" and data.get("is_seo_review"):
            pending_seo_title = data.get("seo_title", "")
            pending_seo_desc = data.get("seo_desc", "")
            
            # Tự động gửi video duyệt trước nếu có đường dẫn video và file tồn tại
            video_path = data.get("video_path")
            if video_path and os.path.exists(video_path):
                file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
                if file_size_mb <= 49.5: # Giới hạn upload của bot là 50MB
                    await send_message(active_chat_id, "📤 <i>Đang tải video thành phẩm lên để bạn xem duyệt trước...</i>")
                    await send_video(active_chat_id, video_path, caption=f"🎬 Video xem trước: {pending_seo_title}")
                else:
                    await send_message(active_chat_id, f"⚠️ <i>Video thành phẩm quá lớn ({file_size_mb:.1f}MB > 50MB) nên không thể gửi trực tiếp qua chat Telegram. Bạn có thể xem trực tiếp tại Dashboard!</i>")
            
            # Gửi tin nhắn duyệt kèm theo inline keyboard
            text = (
                f"📝 <b>KIỂM DUYỆT SEO & TIÊU ĐỀ YOUTUBE</b>\n\n"
                f"📌 <b>Tiêu đề video YouTube gợi ý:</b>\n"
                f"<code>{pending_seo_title}</code>\n\n"
                f"💬 <b>Mô tả SEO gợi ý:</b>\n"
                f"<code>{pending_seo_desc}</code>\n\n"
                f"👉 <i>Bạn có thể chỉnh sửa trước khi đăng:</i>\n"
                f"• Gửi tin nhắn: <code>/title Tiêu đề mới</code> để sửa tiêu đề.\n"
                f"• Gửi tin nhắn: <code>/desc Mô tả mới</code> để sửa mô tả.\n\n"
                f"Chọn hành động bên dưới để tiếp tục:"
            )
            
            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "Phê duyệt & Đăng ngay ✅", "callback_data": "approve_seo"},
                        {"text": "Từ chối / Hủy bỏ ❌", "callback_data": "reject_seo"}
                    ]
                ]
            }
            
            res = await send_message(active_chat_id, text, reply_markup=keyboard)
            if res:
                review_message_id = res.get("message_id")
                
        # 6. Khi hệ thống hoàn thành toàn bộ workflow xuất sắc!
        elif event_type == "status_change" and data.get("status") == "completed":
            workflow_running = False
            await send_message(
                active_chat_id, 
                f"🎉 <b>HOÀN THÀNH XUẤT SẮC WORKFLOW!</b>\n\n"
                f"Video và tiêu đề SEO của bạn đã được tải lên và đồng bộ hóa thành công tuyệt đối lên đám mây 🚀"
            )
            
            # Tìm và gửi file video thành phẩm trực tiếp nếu có
            final_video = None
            for step in reversed(plan_steps):
                if "output_file" in step:
                    final_video = step["output_file"]
                    break
            
            if final_video and os.path.exists(final_video):
                file_size_mb = os.path.getsize(final_video) / (1024 * 1024)
                if file_size_mb <= 49.5: # Giới hạn upload bot thường là 50MB
                    await send_message(active_chat_id, "📤 <i>Đang tải video thành phẩm lên cuộc trò chuyện này...</i>")
                    await send_video(active_chat_id, final_video, caption=f"🎬 Video thành phẩm lồng tiếng & phụ đề: {pending_seo_title}")
                else:
                    await send_message(active_chat_id, f"⚠️ <i>Video thành phẩm quá lớn ({file_size_mb:.1f}MB > 50MB) nên không thể gửi trực tiếp qua chat Telegram. Bạn có thể xem trực tuyến tại mục Output của Dashboard hoặc trên kênh YouTube đã liên kết!</i>")
                    
    except Exception as e:
        print(f"[TelegramBot] Lỗi trong bộ xử lý sự kiện: {str(e)}")
        traceback.print_exc()

# Hàm cập nhật nóng tin nhắn Preview đang kiểm duyệt trên Telegram
async def update_seo_review_preview():
    global active_chat_id, review_message_id, pending_seo_title, pending_seo_desc
    if not active_chat_id or not review_message_id:
        return
        
    text = (
        f"📝 <b>KIỂM DUYỆT SEO & TIÊU ĐỀ YOUTUBE (ĐÃ CẬP NHẬT)</b>\n\n"
        f"📌 <b>Tiêu đề video YouTube chính thức:</b>\n"
        f"<code>{pending_seo_title}</code>\n\n"
        f"💬 <b>Mô tả SEO chính thức:</b>\n"
        f"<code>{pending_seo_desc}</code>\n\n"
        f"👉 <i>Bạn có thể tiếp tục chỉnh sửa:</i>\n"
        f"• Gửi tin nhắn: <code>/title Tiêu đề mới</code> để sửa tiêu đề.\n"
        f"• Gửi tin nhắn: <code>/desc Mô tả mới</code> để sửa mô tả.\n\n"
        f"Chọn hành động bên dưới để tiếp tục:"
    )
    
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "Phê duyệt & Đăng ngay ✅", "callback_data": "approve_seo"},
                {"text": "Từ chối / Hủy bỏ ❌", "callback_data": "reject_seo"}
            ]
        ]
    }
    
    await edit_message(active_chat_id, review_message_id, text, reply_markup=keyboard)

# Vòng lặp Long Polling chính thức của Telegram Bot
async def start_telegram_bot():
    global active_chat_id, status_message_id, review_message_id, plan_steps, workflow_running, goal_url
    global pending_seo_title, pending_seo_desc
    
    if not BOT_TOKEN:
        print("⚠️ [TelegramBot] TELEGRAM_BOT_TOKEN trống. Bỏ qua khởi động Telegram Bot.")
        return
        
    print("🤖 [TelegramBot] Khởi động Telegram Bot Polling Server...")
    
    # Đăng ký listener sự kiện với Event Bus trung tâm của Orchestrator
    from backend.modules.orchestrator import event_listeners
    if telegram_event_handler not in event_listeners:
        event_listeners.append(telegram_event_handler)
        print("🔌 [EventBus] Đăng ký thành công Telegram Event Listener!")
        
    offset = 0
    
    while True:
        try:
            payload = {
                "offset": offset,
                "timeout": 20
            }
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
            
            async with httpx.AsyncClient(timeout=25.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    res = resp.json()
                    updates = res.get("result", [])
                    
                    for update in updates:
                        offset = update.get("update_id", 0) + 1
                        
                        # 1. Xử lý tin nhắn văn bản thông thường
                        if "message" in update:
                            message = update["message"]
                            chat_id = message["chat"]["id"]
                            text = message.get("text", "").strip()
                            
                            # Nhận lệnh /start hoặc /help
                            if text.startswith("/start") or text.startswith("/help"):
                                welcome_msg = (
                                    "👋 <b>Chào mừng bạn đến với Aegis AI Employee System!</b>\n\n"
                                    "Tôi là trợ lý AI điều hành chu trình xử lý tự động (Aegis Workflow Pipeline) của bạn. Cách sử dụng cực kỳ đơn giản:\n\n"
                                    "👉 <b>Chỉ cần gửi bất kỳ link video YouTube hoặc Bilibili nào vào đây!</b>\n"
                                    "Tôi sẽ tự động kích hoạt workflow tối ưu:\n"
                                    "<i>Tải video 1080p ➡️ Tách âm AI ➡️ Dịch phụ đề song song sang Tiếng Việt ➡️ Nhúng phụ đề ASS cứng ➡️ AI viết mô tả SEO gợi ý ➡️ Cho bạn duyệt tiêu đề/SEO ngay trên chat ➡️ Đăng lên YouTube tự động!</i>\n\n"
                                    "🛠️ <b>Lệnh sửa đổi khi Kiểm duyệt SEO:</b>\n"
                                    "• <code>/title Tiêu đề mới</code>: Sửa tiêu đề YouTube đang duyệt.\n"
                                    "• <code>/desc Mô tả mới</code>: Sửa nội dung mô tả/hastags đang duyệt."
                                )
                                await send_message(chat_id, welcome_msg)
                                continue
                                
                            # Sửa đổi tiêu đề đang duyệt
                            elif text.startswith("/title ") and review_message_id:
                                new_title = text[7:].strip()
                                if new_title:
                                    pending_seo_title = new_title
                                    # Ghi nhận vào orchestrator để lưu trữ
                                    import backend.modules.orchestrator as orch
                                    orch.approved_seo_title = pending_seo_title
                                    await send_message(chat_id, "✏️ <i>Đã cập nhật tiêu đề YouTube mới!</i>")
                                    await update_seo_review_preview()
                                continue
                                
                            # Sửa đổi mô tả đang duyệt
                            elif text.startswith("/desc ") and review_message_id:
                                new_desc = text[6:].strip()
                                if new_desc:
                                    pending_seo_desc = new_desc
                                    # Ghi nhận vào orchestrator để lưu trữ
                                    import backend.modules.orchestrator as orch
                                    orch.approved_seo_desc = pending_seo_desc
                                    await send_message(chat_id, "✏️ <i>Đã cập nhật nội dung mô tả SEO mới!</i>")
                                    await update_seo_review_preview()
                                continue
                                
                            # Phát hiện liên kết gửi vào
                            url_match = URL_PATTERN.search(text)
                            if url_match:
                                video_url = url_match.group(1)
                                
                                # Xác minh xem URL có thuộc nền tảng video được hỗ trợ hay không
                                is_supported = False
                                for domain in ["bilibili.com", "youtube.com", "youtu.be", "b23.tv"]:
                                    if domain in video_url.lower():
                                        is_supported = True
                                        break
                                
                                if not is_supported:
                                    continue
                                    
                                if workflow_running:
                                    await send_message(chat_id, "⚠️ Hệ thống đang bận thực hiện một video khác. Vui lòng đợi trong giây lát...")
                                    continue
                                    
                                active_chat_id = chat_id
                                goal_url = video_url
                                status_message_id = None
                                review_message_id = None
                                workflow_running = True
                                
                                await send_message(chat_id, f"📥 <b>Đã nhận link:</b> <code>{video_url}</code>\n⚡ <i>Đang khởi chạy Aegis Flow Engine. Vui lòng đợi trong giây lát...</i>")
                                
                                # 1. Ưu tiên tuyệt đối nạp cấu hình Node tùy biến từ bộ nhớ Orchestrator (từ WebUI đang kết nối) hoặc file cấu hình WebUI
                                from backend.main import agent
                                import copy
                                nodes_data = None
                                
                                if hasattr(agent, "active_nodes_data") and agent.active_nodes_data:
                                    nodes_data = copy.deepcopy(agent.active_nodes_data)
                                    print("🔌 [TelegramBot] Đã nạp thành công cấu hình LIVE Node từ bộ nhớ Orchestrator!")
                                
                                if not nodes_data:
                                    from backend.config import TEMP_DIR
                                    config_file = TEMP_DIR / "webui_config.json"
                                    if config_file.exists():
                                        try:
                                            with open(config_file, "r", encoding="utf-8") as f:
                                                loaded_data = json.load(f)
                                                nodes_data = loaded_data.get("nodes")
                                                print("🔌 [TelegramBot] Đã nạp cấu hình Node từ file webui_config.json!")
                                        except Exception as e:
                                            print(f"⚠️ [TelegramBot] Lỗi nạp cấu hình file WebUI: {str(e)}")
                                        
                                # 2. Dự phòng cấu hình Node mặc định tối ưu nhất (Trùng khớp hoàn hảo với WebUI)
                                if not nodes_data:
                                    nodes_data = {
                                        "downloader": {"config": {"quality": "best"}, "bypassed": False},
                                        "audio": {"config": {"enhance": True}, "bypassed": False},
                                        "subtitle": {"config": {"model": "large-v3", "device": "cuda", "offset": 0.0}, "bypassed": False},
                                        "translator": {"config": {"target_lang": "Tiếng Việt", "provider": "ollama", "model": "deepseek-r1:8b", "api_base": "http://localhost:11434", "api_key": ""}, "bypassed": False},
                                        "voiceover": {"config": {"engine": "edge", "voice": "vi-VN-HoaiMyNeural", "emotion": "neutral", "api_key_openai": "", "api_key_elevenlabs": "", "mix_ratio": 70}, "bypassed": True},
                                        "exporter": {"config": {"dual_subtitles": False, "fontname": "Outfit", "fontsize": 16, "color": "#00FFFF", "outline_color": "#000000", "borderstyle": "1"}, "bypassed": False},
                                        "seo": {"config": {"platform": "youtube", "tone": "engaging"}, "bypassed": False},
                                        "clipper": {"config": {"duration": 60, "aspect_ratio": "9:16"}, "bypassed": True},
                                        "uploader": {"config": {"gdrive": False, "telegram": True, "youtube": True, "privacy_status": "private"}, "bypassed": False}
                                    }
                                
                                # 3. Trích xuất các tham số upload và style trực tiếp từ cấu hình Node đã nạp (Thống nhất tuyệt đối)
                                uploader_bypassed = nodes_data.get("uploader", {}).get("bypassed", False)
                                pub_cfg = nodes_data.get("uploader", {}).get("config", {})
                                
                                if uploader_bypassed:
                                    upload_gdrive = False
                                    upload_telegram = False
                                    upload_youtube = False
                                else:
                                    upload_gdrive = pub_cfg.get("gdrive", False)
                                    upload_telegram = pub_cfg.get("telegram", True)
                                    upload_youtube = pub_cfg.get("youtube", True)
                                
                                exp_config = nodes_data.get("exporter", {}).get("config", {})
                                fontname = exp_config.get("fontname", "Outfit")
                                fontsize = exp_config.get("fontsize", 16)
                                color = exp_config.get("color", "#00FFFF")
                                outline_color = exp_config.get("outline_color", "#000000")
                                borderstyle = exp_config.get("borderstyle", "1")
                                
                                # Hàm chuyển đổi mã màu hex sang ass style color
                                def hex_to_ass(hex_str):
                                    if not hex_str or not hex_str.startswith("#") or len(hex_str) < 7:
                                        return "&H00FFFF"
                                    r = hex_str[1:3]
                                    g = hex_str[3:5]
                                    b = hex_str[5:7]
                                    return f"&H00{b}{g}{r}"
                                    
                                style_str = f"FontSize={fontsize},PrimaryColour={hex_to_ass(color)},OutlineColour={hex_to_ass(outline_color)},BorderStyle={borderstyle},Fontname={fontname}"
                                
                                # Khởi chạy Workflow trong Central Orchestrator
                                goal_str = f"tải video từ link {video_url} và tạo phụ đề lồng tiếng đăng youtube"
                                asyncio.create_task(agent.execute_workflow(
                                    goal_str, nodes_data, [],
                                    subtitle_style=style_str, 
                                    upload_gdrive=upload_gdrive, 
                                    upload_telegram=upload_telegram, 
                                    upload_youtube=upload_youtube
                                ))
                                continue
                                
                        # 2. Xử lý sự kiện Callback Nút bấm nội tuyến (Inline buttons)
                        elif "callback_query" in update:
                            cb = update["callback_query"]
                            cb_id = cb["id"]
                            chat_id = cb["message"]["chat"]["id"]
                            msg_id = cb["message"]["message_id"]
                            cb_data = cb.get("data")
                            
                            # Xác nhận phê duyệt đăng bài
                            if cb_data == "approve_seo":
                                import backend.modules.orchestrator as orch
                                # Đẩy dữ liệu đã chỉnh sửa hoặc thô gốc vào
                                orch.approved_seo_title = pending_seo_title
                                orch.approved_seo_desc = pending_seo_desc
                                orch.approval_decision = "approved"
                                orch.approval_comment = ""
                                orch.approval_event.set()
                                
                                # Trả lời Callback để tắt đồng hồ xoay
                                await call_telegram_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Đã phê duyệt đăng lên YouTube!"})
                                await edit_message(chat_id, msg_id, f"✅ <b>ĐÃ PHÊ DUYỆT THÀNH CÔNG!</b>\n\nTiêu đề chính thức: <i>{pending_seo_title}</i>\n\n⚡ <i>Hệ thống đang tiến hành đăng video lên kênh YouTube của bạn thời gian thực...</i>")
                                review_message_id = None
                                
                            # Từ chối đăng bài
                            elif cb_data == "reject_seo":
                                import backend.modules.orchestrator as orch
                                orch.approval_decision = "rejected"
                                orch.approval_comment = "Người dùng từ chối trực tiếp qua chat Telegram."
                                orch.approval_event.set()
                                
                                await call_telegram_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Đã hủy đăng!"})
                                await edit_message(chat_id, msg_id, "❌ <b>ĐÃ HỦY ĐĂNG!</b>\n\nQuy trình làm việc đã bị dừng lại và video tạm thời lưu trong kho lưu trữ.")
                                review_message_id = None
                                workflow_running = False
                                
        except Exception as err:
            print(f"[TelegramBot] Lỗi trong vòng lặp polling: {str(err)}")
            traceback.print_exc()
            
        await asyncio.sleep(1.0)

if __name__ == "__main__":
    try:
        asyncio.run(start_telegram_bot())
    except KeyboardInterrupt:
        print("🤖 [TelegramBot] Đã dừng Bot thủ công.")
