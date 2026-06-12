import json
import re
from backend.modules.orchestrator import call_llm

class Planner:
    def __init__(self):
        pass

    async def generate_plan(self, goal: str, past_experiences: str = "") -> list:
        """
        Dùng DeepSeek để phân tách mục tiêu lớn (Goal) thành các bước kế hoạch chi tiết.
        """
        system_prompt = (
            "You are the central planning core of an AI Employee. Your job is to take a high-level goal "
            "and decompose it into a sequence of actionable steps using the available system modules."
        )

        prompt = f"""
Mục tiêu cần hoàn thành: "{goal}"

Các mô-đun hệ thống sẵn có mà bạn có thể sử dụng:
1. Mô-đun Tải Video:
   - `module`: "downloader"
   - `action`: "download"
   - `params`: {{"url": "đường_dẫn_video_youtube_hoặc_khác"}}
   - Lưu ý: Dùng để tải video từ Youtube, TikTok, Facebook, v.v.

2. Mô-đun Phụ Đề & Biên Dịch:
   - `module`: "subtitle"
   - `action`: "transcribe" (Trích xuất phụ đề gốc từ video, params: {{"video_path": "đường_dẫn_video"}})
   - `action`: "translate" (Dịch phụ đề, params: {{"srt_path": "đường_dẫn_srt", "target_lang": "Tiếng Việt"}})
   - `action`: "burn" (Nhúng phụ đề vào video bằng FFmpeg, params: {{"video_path": "đường_dẫn_video", "srt_path": "đường_dẫn_srt"}})
   - Lưu ý: Các tham số đường dẫn (video_path, srt_path) là tùy chọn nếu chúng kế thừa từ các bước trước đó.

3. Mô-đun Trình Duyệt Playwright:
   - `module`: "browser"
   - `action`: "interact"
   - `params`: {{"url": "trang_web_cần_mở", "task": "mô_tả_nhiệm_vụ_cần_thực_hiện"}}
   - Lưu ý: Sử dụng khi cần tra cứu thông tin trực tuyến, đăng bài viết, tương tác với web.

Hãy xem xét các bài học kinh nghiệm từ quá khứ (nếu có):
{past_experiences if past_experiences else "(Không có kinh nghiệm cũ nào tương thích. Hãy lên kế hoạch cẩn thận!)"}

---
YÊU CẦU ĐẦU RA:
Bạn phải trả về một danh sách các bước kế hoạch hợp lệ theo định dạng JSON nguyên bản (JSON array of objects).
Tuyệt đối KHÔNG trả về bất kỳ giải thích, nhận xét hay định dạng markdown nào khác ngoài chuỗi JSON.
Mỗi bước trong mảng phải chứa chính xác các trường sau:
- `title`: Tên hiển thị của bước (ví dụ: "Tải video giới thiệu AI từ YouTube")
- `module`: Tên mô-đun sử dụng ("downloader", "subtitle", hoặc "browser")
- `action`: Hành động cụ thể ("download", "transcribe", "translate", "burn", hoặc "interact")
- `params`: Object chứa các tham số tương ứng với hành động
- `requires_approval`: Giá trị boolean (`true` hoặc `false`). Hãy đặt là `true` nếu bước đó thực hiện các thao tác nhạy cảm trên trình duyệt (như viết bài viết, gửi form, đăng nhập, hoặc thực hiện thay đổi dữ liệu lớn).

Định dạng mẫu:
[
  {{
    "title": "Tải video giới thiệu",
    "module": "downloader",
    "action": "download",
    "params": {{"url": "https://youtube.com/..."}},
    "requires_approval": false
  }}
]
"""

        # Gọi DeepSeek để lập kế hoạch
        raw_output = await call_llm(prompt, system_prompt=system_prompt)
        
        # Làm sạch chuỗi JSON nhận được
        clean_json = self.clean_json_output(raw_output)
        
        try:
            plan = json.loads(clean_json)
            if not isinstance(plan, list):
                raise ValueError("Kết quả trả về không phải là một List các bước")
            
            # Chuẩn hóa trạng thái ban đầu của mỗi bước
            for step in plan:
                step['status'] = 'pending' # pending, running, completed, failed
                step['result'] = ''
                if 'requires_approval' not in step:
                    step['requires_approval'] = False
                    
            return plan
        except Exception as e:
            print(f"[Planner Error] Lỗi phân tích JSON kế hoạch: {str(e)}")
            print(f"Nội dung nhận được ban đầu: {raw_output}")
            print(f"Nội dung sau khi làm sạch: {clean_json}")
            
            # Trả về kế hoạch mặc định dự phòng nếu DeepSeek trả về sai định dạng
            return self.get_fallback_plan(goal)

    def clean_json_output(self, text: str) -> str:
        """
        Làm sạch markdown codeblocks và các ký tự thừa để trả về JSON chuẩn.
        """
        text = text.strip()
        # Loại bỏ ```json ... ```
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
            
        text = text.strip()
        
        # Tìm mảng JSON đầu tiên trong văn bản đề phòng LLM vẫn viết lời mở đầu
        start_idx = text.find('[')
        end_idx = text.rfind(']')
        if start_idx != -1 and end_idx != -1 and start_idx < end_idx:
            text = text[start_idx:end_idx + 1]
            
        return text

    def get_fallback_plan(self, goal: str) -> list:
        """
        Kế hoạch dự phòng cơ bản nếu quá trình lập kế hoạch tự động của LLM bị lỗi định dạng.
        """
        # Thử phân tích mục tiêu để tìm URL phục vụ fallback
        urls = re.findall(r'(https?://\S+)', goal)
        target_url = urls[0] if urls else "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        
        return [
            {
                "title": "Tải video từ nguồn trực tuyến",
                "module": "downloader",
                "action": "download",
                "params": {"url": target_url},
                "requires_approval": False,
                "status": "pending",
                "result": ""
            },
            {
                "title": "Nhận diện giọng nói để xuất phụ đề gốc",
                "module": "subtitle",
                "action": "transcribe",
                "params": {},
                "requires_approval": False,
                "status": "pending",
                "result": ""
            },
            {
                "title": "Biên dịch phụ đề sang tiếng Việt mượt mà",
                "module": "subtitle",
                "action": "translate",
                "params": {"target_lang": "Tiếng Việt"},
                "requires_approval": False,
                "status": "pending",
                "result": ""
            },
            {
                "title": "Nhúng phụ đề tiếng Việt vào video bằng FFmpeg",
                "module": "subtitle",
                "action": "burn",
                "params": {},
                "requires_approval": False,
                "status": "pending",
                "result": ""
            }
        ]
