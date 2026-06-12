import os
import sys
import io
import asyncio
import subprocess
from pathlib import Path

# Force stdout and stderr to use UTF-8 encoding on Windows to prevent UnicodeEncodeErrors with Vietnamese
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding='utf-8')

# Đảm bảo sử dụng WindowsProactorEventLoopPolicy trên Windows để tương thích với Playwright
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# Tự động thêm thư mục gốc dự án (project root) vào sys.path
backend_dir = Path(__file__).resolve().parent
project_root = backend_dir.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
import uvicorn

from backend.config import OUTPUT_DIR, TEMP_DIR, BASE_DIR
from backend.modules import orchestrator as orch
from backend.modules.orchestrator import Orchestrator
from backend.routes.modules_api import router as modules_api_router

app = FastAPI(title="AI Employee System API", version="1.0.0")

# Đăng ký Router API cho các Module nội bộ
app.include_router(modules_api_router)

# Cấu hình CORS để frontend tương tác thoải mái
app.add_middleware(

    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Thư mục frontend
FRONTEND_DIR = BASE_DIR / "frontend"

# Tự động mount thư mục output để frontend xem video kết quả trực tuyến
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")

# Khởi tạo Bộ điều phối trung tâm
agent = Orchestrator()

def get_system_metrics():
    """
    Thu thập các chỉ số CPU, RAM từ hệ thống và GPU từ nvidia-smi.
    """
    # Chỉ số mặc định dự phòng
    cpu = 15.0
    ram = 45.0
    try:
        import psutil
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory().percent
    except ImportError:
        pass

    # Đo lường GPU RTX Quadro 6000 24GB VRAM
    gpu = 0.0
    gpu_name = "NVIDIA GPU"
    gpu_vram = "0 / 24 GB"
    try:
        res = subprocess.run(
            ['nvidia-smi', '--query-gpu=utilization.gpu,memory.used,memory.total,gpu_name', '--format=csv,noheader,nounits'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        if res.returncode == 0:
            parts = res.stdout.strip().split(',')
            if len(parts) >= 4:
                gpu_val = parts[0].strip()
                vram_used = parts[1].strip()
                vram_total = parts[2].strip()
                name_val = parts[3].strip()
                
                try:
                    gpu = float(gpu_val)
                except ValueError:
                    gpu = 0.0
                
                try:
                    used_gb = float(vram_used) / 1024
                    total_gb = float(vram_total) / 1024
                    gpu_vram = f"{used_gb:.1f} / {total_gb:.1f} GB"
                    # Lấy phần trăm VRAM sử dụng để biểu diễn tỉ lệ load vòng tròn
                    gpu = round((float(vram_used) / float(vram_total)) * 100, 1)
                except Exception:
                    pass
                gpu_name = name_val
    except Exception:
        pass
        
    return {
        "cpu": cpu,
        "ram": ram,
        "gpu": gpu,
        "gpu_name": gpu_name,
        "gpu_vram": gpu_vram
    }

async def broadcast_metrics_loop():
    """
    Vòng lặp gửi chỉ số CPU, RAM, GPU thời gian thực lên Dashboard qua WebSocket mỗi 2 giây.
    """
    while True:
        await asyncio.sleep(2.0)
        if orch.active_sockets:
            try:
                metrics = get_system_metrics()
                await orch.broadcast_event("metrics", metrics)
            except Exception:
                pass

@app.on_event("startup")
async def startup_event():
    # Khởi chạy luồng đo lường trong background
    asyncio.create_task(broadcast_metrics_loop())
    
    # Khởi chạy Telegram Bot daemon trong background
    try:
        from backend.telegram_bot import start_telegram_bot
        asyncio.create_task(start_telegram_bot())
        print("🤖 [TelegramBot] Khởi động tác vụ chạy ngầm Telegram Bot thành công.")
    except Exception as e:
        print(f"❌ [TelegramBot] Không thể tải Telegram Bot: {str(e)}")

@app.get("/")
def read_root():
    """Redirect trang chủ sang Dashboard"""
    return RedirectResponse(url="/app/index.html")

@app.get("/api/status")
def get_status():
    """
    Lấy trạng thái tổng quan hiện tại của AI.
    """
    return {
        "status": orch.current_task_status,
        "goal": agent.current_goal,
        "step_index": agent.current_step_index,
        "plan": agent.plan_steps
    }

@app.get("/api/metrics")
def get_metrics_endpoint():
    """
    Endpoint HTTP lấy chỉ số tài nguyên hệ thống trực tiếp.
    """
    return get_system_metrics()

@app.get("/api/media-library")
def get_media_library():
    """
    Lấy danh sách các tệp video thành phẩm .mp4 và phụ đề .srt từ OUTPUT_DIR.
    """
    if not OUTPUT_DIR.exists():
        return []
    
    files = []
    for f in OUTPUT_DIR.glob("*"):
        if f.is_file() and f.suffix.lower() in [".mp4", ".srt"]:
            stat = f.stat()
            files.append({
                "name": f.name,
                "suffix": f.suffix,
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "modified": stat.st_mtime,
                "path": f"/output/{f.name}"
            })
    # Sắp xếp theo thứ tự mới nhất lên trước
    files.sort(key=lambda x: x["modified"], reverse=True)
    return files

@app.delete("/api/media-library/{filename}")
def delete_media_file(filename: str):
    """
    Xóa một tệp tin trong thư mục output.
    """
    file_path = OUTPUT_DIR / filename
    # Bảo mật: không cho phép thoát khỏi thư mục output bằng đường dẫn tương đối (ví dụ: ../)
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Tên tệp không hợp lệ.")
        
    if file_path.exists() and file_path.is_file():
        try:
            os.remove(file_path)
            return {"success": True, "message": f"Đã xóa tệp {filename}."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Không thể xóa tệp: {str(e)}")
    raise HTTPException(status_code=404, detail="Không tìm thấy tệp tin.")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Kênh WebSocket kết nối hai chiều thời gian thực với Dashboard.
    """
    await websocket.accept()
    orch.active_sockets.add(websocket)
    print(f"[WebSocket] Client mới kết nối. Tổng số client hoạt động: {len(orch.active_sockets)}")
    
    # Gửi trạng thái hiện tại ngay lập tức khi client kết nối
    await websocket.send_json({
        "type": "init_state",
        "data": {
            "status": orch.current_task_status,
            "goal": agent.current_goal,
            "step_index": agent.current_step_index,
            "plan": agent.plan_steps,
            "nodes_data": agent.active_nodes_data
        }
    })
    
    try:
        while True:
            # Chờ nhận tin nhắn từ Dashboard
            data = await websocket.receive_json()
            msg_type = data.get("type")
            payload = data.get("data", {})
            
            if msg_type == "start_goal":
                goal = payload.get("goal", "")
                api_config = payload.get("api_config")
                if goal:
                    # Khởi chạy trong Background task của asyncio để không block luồng WebSocket chính
                    asyncio.create_task(agent.execute_goal(goal, api_config))
                    
            elif msg_type == "start_workflow":
                goal = payload.get("goal", "")
                nodes_data = payload.get("nodes", {})
                connections_data = payload.get("connections", [])
                subtitle_style = payload.get("subtitle_style", "")
                upload_gdrive = payload.get("upload_gdrive", False)
                upload_telegram = payload.get("upload_telegram", False)
                upload_youtube = payload.get("upload_youtube", False)
                # Lưu srt_content đã sửa đổi qua Subtitle Editor trước khi chạy nếu có
                orch.edited_srt_content = payload.get("edited_srt_content", "")
                if goal:
                    asyncio.create_task(agent.execute_workflow(
                        goal, nodes_data, connections_data, 
                        subtitle_style, upload_gdrive, upload_telegram, upload_youtube
                    ))
                    
            elif msg_type == "auto_wrap_subtitle":
                srt_content = payload.get("srt_content", "")
                if srt_content:
                    from backend.modules.exporter import SubtitleExporter
                    exporter = SubtitleExporter()
                    wrapped_srt = exporter.auto_wrap_srt(srt_content)
                    await websocket.send_json({
                        "type": "srt_ready",
                        "data": {"srt_content": wrapped_srt, "is_translated": True}
                    })

            elif msg_type == "save_webui_config":
                nodes_data = payload.get("nodes", {})
                connections_data = payload.get("connections", [])
                if nodes_data:
                    config_file = TEMP_DIR / "webui_config.json"
                    try:
                        import json
                        with open(config_file, "w", encoding="utf-8") as f:
                            json.dump({"nodes": nodes_data, "connections": connections_data}, f, ensure_ascii=False, indent=4)
                        # Đồng thời lưu trữ tạm vào memory của orchestrator
                        agent.active_nodes_data = nodes_data
                    except Exception as e:
                        print(f"[WebSocket] Lỗi khi lưu cấu hình WebUI: {str(e)}")
                        
            elif msg_type == "approve":
                # Ghi trực tiếp vào biến module-level của orchestrator
                orch.approval_decision = "approved"
                orch.approval_comment = ""
                # Lưu srt_content đã sửa đổi qua Subtitle Editor nếu có
                orch.edited_srt_content = payload.get("srt_content", "")
                # Lưu SEO Title và Description được duyệt nếu có
                orch.approved_seo_title = payload.get("seo_title", "")
                orch.approved_seo_desc = payload.get("seo_desc", "")
                orch.approval_event.set()
                print("[WebSocket] Nhận được tín hiệu PHÊ DUYỆT từ người dùng.")
                
            elif msg_type == "reject":
                orch.approval_decision = "rejected"
                orch.approval_comment = payload.get("comment", "Không có lý do cụ thể.")
                orch.approval_event.set()
                print(f"[WebSocket] Nhận được tín hiệu TỪ CHỐI từ người dùng. Lý do: {orch.approval_comment}")
                
    except WebSocketDisconnect:
        orch.active_sockets.discard(websocket)
        print(f"[WebSocket] Client ngắt kết nối. Tổng số client còn lại: {len(orch.active_sockets)}")
    except Exception as e:
        orch.active_sockets.discard(websocket)
        print(f"[WebSocket] Lỗi kết nối: {str(e)}")

# Mount frontend Dashboard tại /app (PHẢI đặt sau tất cả các route khác)
app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

def run_server():
    """
    Hàm khởi chạy server uvicorn
    """
    backend_dir = str(Path(__file__).resolve().parent)
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True, reload_dirs=[backend_dir])

if __name__ == "__main__":
    run_server()
