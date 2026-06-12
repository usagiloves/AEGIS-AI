import os
import json
import asyncio
import base64
from pathlib import Path
from playwright.async_api import async_playwright
from backend.config import PLAYWRIGHT_HEADLESS, BROWSER_TIMEOUT, TEMP_DIR
from backend.modules.orchestrator import broadcast_event, call_llm

class BrowserAgent:
    def __init__(self):
        self.screenshot_dir = TEMP_DIR / "screenshots"
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

    async def _log(self, message):
        await broadcast_event("log", {"level": "info", "message": f"[BrowserAgent] {message}"})

    async def run_browser_task(self, url: str, task_desc: str) -> tuple[bool, str]:
        """
        Khởi chạy trình duyệt Playwright, duyệt qua trang web, chụp ảnh màn hình, 
        và dùng DeepSeek đưa ra các hành động click/nhập liệu thông minh từng bước.
        """
        await self._log(f"Đang chuẩn bị khởi chạy Playwright (Headless={PLAYWRIGHT_HEADLESS})...")
        
        try:
            async with async_playwright() as p:
                # Khởi chạy trình duyệt chromium
                browser = await p.chromium.launch(
                    headless=PLAYWRIGHT_HEADLESS,
                    slow_mo=1000 # Chậm lại 1s để người dùng dễ nhìn thấy
                )
                
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
                
                page = await context.new_page()
                page.set_default_timeout(BROWSER_TIMEOUT)
                
                await self._log(f"Đang mở trang: {url}...")
                await page.goto(url)
                await page.wait_for_load_state("networkidle")
                
                step_limit = 5
                current_step = 0
                last_action_desc = "Khởi động trang web."
                
                while current_step < step_limit:
                    current_step += 1
                    await self._log(f"--- Vòng lặp hành động {current_step}/{step_limit} ---")
                    
                    # 1. Chụp ảnh màn hình hiện tại
                    screenshot_path = self.screenshot_dir / f"step_{current_step}.png"
                    await page.screenshot(path=str(screenshot_path))
                    
                    # Đọc ảnh màn hình để phát qua WebSocket (hiển thị trực tiếp trên Dashboard!)
                    with open(screenshot_path, "rb") as image_file:
                        encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                    await broadcast_event("browser_screenshot", {"image": encoded_string, "step": current_step})
                    
                    # 2. Thu thập cấu trúc DOM tương tác rút gọn
                    elements = await page.evaluate("""() => {
                        const items = [];
                        // Lấy các thẻ nhập liệu, nút bấm, liên kết hiển thị trên màn hình
                        const selector = 'input, button, a, [role="button"]';
                        const elList = document.querySelectorAll(selector);
                        
                        let idx = 0;
                        elList.forEach(el => {
                            const rect = el.getBoundingClientRect();
                            // Chỉ lấy các phần tử hiển thị thực sự
                            if (rect.width > 0 && rect.height > 0 && window.getComputedStyle(el).display !== 'none') {
                                items.push({
                                    index: idx++,
                                    tag: el.tagName.toLowerCase(),
                                    text: el.innerText.trim() || el.getAttribute('placeholder') || el.value || '',
                                    id: el.id || '',
                                    class: el.className || '',
                                    name: el.getAttribute('name') || ''
                                });
                            }
                        });
                        return items.slice(0, 40); // Giới hạn 40 phần tử để tránh quá tải Token
                    }""")
                    
                    dom_str = json.dumps(elements, ensure_ascii=False, indent=2)
                    
                    # 3. Gửi thông tin về trang cho DeepSeek và yêu cầu hành động tiếp theo
                    prompt = f"""
Nhiệm vụ tổng thể: "{task_desc}"
Trạng thái trang hiện tại: URL = {page.url}
Hành động vừa thực hiện trước đó: {last_action_desc}

Danh sách các phần tử tương tác hiển thị trên màn hình (định dạng JSON):
{dom_str}

---
YÊU CẦU:
Hãy phân tích cấu trúc trang web và quyết định hành động tiếp theo để hoàn thành nhiệm vụ tổng thể.
Bạn chỉ được chọn MỘT trong các loại hành động sau:
1. CLICK vào một phần tử: trả về định dạng `CLICK: <index_của_phần_tử_trong_json>`
2. NHẬP LIỆU vào ô nhập: trả về định dạng `TYPE: <index_của_phần_tử_trong_json>: <nội_dung_cần_nhập>`
3. CUỘN TRANG xuống: trả về định dạng `SCROLL: DOWN`
4. HOÀN THÀNH nhiệm vụ: trả về định dạng `COMPLETE: <tóm_tắt_thông_tin_đã_thu_thập_hoặc_kết_quả>`

Quy tắc:
- Chỉ trả về chuỗi lệnh ngắn gọn theo định dạng chính xác ở trên (Không thêm bất kỳ chữ nào khác, không dùng codeblock).
- Ví dụ: CLICK: 3
- Ví dụ: TYPE: 1: lập trình python nâng cao
- Ví dụ: COMPLETE: Đã tìm thấy giá sản phẩm là 15,000,000 VND.
"""
                    await self._log("Đang phân tích trang và đưa ra quyết định hành động bằng DeepSeek...")
                    decision = await call_llm(prompt, system_prompt="You are a browser automation controller. Give exact command outputs.")
                    decision = decision.strip()
                    
                    await self._log(f"Quyết định của AI: '{decision}'")
                    
                    # 4. Thực thi hành động tương ứng
                    if decision.startswith("CLICK:"):
                        try:
                            idx = int(decision.split(":")[1].strip())
                            target_el = elements[idx]
                            # Xây dựng Selector động từ thuộc tính thu thập
                            selector = ""
                            if target_el['id']:
                                selector = f"#{target_el['id']}"
                            elif target_el['name']:
                                selector = f"{target_el['tag']}[name='{target_el['name']}']"
                            else:
                                selector = f"{target_el['tag']}:has-text('{target_el['text']}')" if target_el['text'] else f"{target_el['tag']}"
                                
                            await self._log(f"Đang click phần tử: {target_el['tag']} '{target_el['text']}' (Selector: {selector})...")
                            # Đợi tải trang nếu click gây ra chuyển trang
                            await page.click(selector)
                            await page.wait_for_timeout(2000) # Đợi 2 giây phản hồi
                            last_action_desc = f"Đã click phần tử index {idx}: {target_el['text']}"
                        except Exception as click_err:
                            last_action_desc = f"Lỗi khi click index: {str(click_err)}"
                            await self._log(f"Lỗi thao tác click: {last_action_desc}")
                            
                    elif decision.startswith("TYPE:"):
                        try:
                            parts = decision.split(":")
                            idx = int(parts[1].strip())
                            text_to_type = ":".join(parts[2:]).strip()
                            target_el = elements[idx]
                            
                            selector = f"#{target_el['id']}" if target_el['id'] else f"input[name='{target_el['name']}']" if target_el['name'] else "input"
                            
                            await self._log(f"Đang nhập '{text_to_type}' vào ô: {target_el['text']}...")
                            await page.fill(selector, text_to_type)
                            await page.press(selector, "Enter")
                            await page.wait_for_timeout(2000)
                            last_action_desc = f"Đã nhập '{text_to_type}' vào phần tử index {idx}"
                        except Exception as type_err:
                            last_action_desc = f"Lỗi khi nhập liệu: {str(type_err)}"
                            await self._log(f"Lỗi thao tác nhập: {last_action_desc}")
                            
                    elif decision.startswith("SCROLL:"):
                        await self._log("Đang cuộn trang xuống...")
                        await page.evaluate("window.scrollBy(0, 400)")
                        await page.wait_for_timeout(1000)
                        last_action_desc = "Đã cuộn trang xuống 400px."
                        
                    elif decision.startswith("COMPLETE:"):
                        result_text = decision.replace("COMPLETE:", "").strip()
                        await browser.close()
                        return True, f"Nhiệm vụ trình duyệt hoàn tất. Kết quả thu thập: {result_text}"
                    else:
                        await self._log(f"Hành động không rõ: '{decision}'. Cuộn trang mặc định.")
                        await page.evaluate("window.scrollBy(0, 200)")
                        last_action_desc = "Hành động không xác định, tự động cuộn trang 200px."
                
                await browser.close()
                return True, "Nhiệm vụ kết thúc do đạt giới hạn bước thao tác trình duyệt."
                
        except Exception as browser_err:
            import traceback
            tb = traceback.format_exc()
            print(f"[BrowserAgent Exception]\n{tb}")
            error_message = str(browser_err) or repr(browser_err)
            await self._log(f"Lỗi Playwright nghiêm trọng: {error_message}")
            await self._log("Mẹo: Đảm bảo bạn đã cài đặt trình duyệt của Playwright bằng cách chạy lệnh: python -m playwright install")
            return False, f"Lỗi Trình duyệt: {error_message}"
