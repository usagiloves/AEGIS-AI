import os
import asyncio
from backend.modules.orchestrator import call_llm

class SubtitleTranslator:
    def __init__(self, progress_callback=None):
        self.progress_callback = progress_callback

    def _log(self, message):
        if self.progress_callback:
            if asyncio.iscoroutinefunction(self.progress_callback):
                try:
                    loop = asyncio.get_running_loop()
                    asyncio.run_coroutine_threadsafe(self.progress_callback({"status": "info", "message": message}), loop)
                except RuntimeError:
                    asyncio.run(self.progress_callback({"status": "info", "message": message}))
            else:
                self.progress_callback({"status": "info", "message": message})
        else:
            print(f"[SubtitleTranslator] {message}")

    async def translate_srt(self, srt_content, target_lang="Tiếng Việt"):
        """
        Dịch file phụ đề SRT sử dụng LLM.
        Sử dụng kỹ thuật chia nhỏ file và dịch song song để tối ưu tốc độ vượt trội.
        """
        self._log(f"Đang dịch phụ đề sang {target_lang}...")
        lines = srt_content.split('\n')
        chunks = []
        current_chunk = []
        line_count = 0
        
        for line in lines:
            current_chunk.append(line)
            if line.strip() == "":
                line_count += 1
                if line_count >= 15:
                    chunks.append("\n".join(current_chunk))
                    current_chunk = []
                    line_count = 0
        if current_chunk:
            chunks.append("\n".join(current_chunk))

        async def translate_chunk(idx, chunk):
            self._log(f"Đang dịch phần {idx}/{len(chunks)}...")
            prompt = (
                f"Bạn là một chuyên gia biên dịch phụ đề phim chuyên nghiệp. Hãy dịch file phụ đề SRT sau đây sang {target_lang}.\n"
                f"Yêu cầu:\n"
                f"1. GIỮ NGUYÊN các mốc thời gian (ví dụ: 00:01:23,450 --> 00:01:25,600) và số thứ tự.\n"
                f"2. Dịch nghĩa tự nhiên, mượt mà, phù hợp với văn phong bản xứ.\n"
                f"3. CHỈ TRẢ VỀ nội dung SRT đã dịch, tuyệt đối không thêm bớt bất cứ bình luận hay giải thích nào.\n\n"
                f"Nội dung cần dịch:\n{chunk}"
            )
            try:
                translated_chunk = await call_llm(prompt, system_prompt="You are a professional subtitle translator. Output only the raw translated SRT content without codeblocks.")
                translated_chunk = translated_chunk.replace("```srt", "").replace("```", "").strip()
                self._log(f"Đã dịch xong phần {idx}/{len(chunks)}!")
                return translated_chunk
            except Exception as e:
                self._log(f"Lỗi khi dịch phần {idx}, giữ nguyên bản gốc: {str(e)}")
                return chunk

        tasks = [translate_chunk(idx, chunk) for idx, chunk in enumerate(chunks, 1)]
        translated_chunks = await asyncio.gather(*tasks)

        self._log("Dịch phụ đề thành công!")
        return "\n\n".join(translated_chunks)

if __name__ == "__main__":
    import argparse
    import sys
    from pathlib import Path
    
    # Đảm bảo UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding='utf-8')
        
    parser = argparse.ArgumentParser(description="AEGIS Subtitle Translator CLI (LLM-Powered)")
    parser.add_argument("-i", "--input", required=True, help="Path to input SRT file")
    parser.add_argument("-o", "--output", default=None, help="Path to output SRT file")
    parser.add_argument("-l", "--lang", default="Tiếng Việt", help="Target translation language (default: Tiếng Việt)")
    
    # Cho phép ghi đè API tạm thời để chạy CLI linh hoạt
    parser.add_argument("--provider", default=None, choices=["ollama", "deepseek", "openrouter"], help="Override LLM Provider")
    parser.add_argument("--model", default=None, help="Override LLM Model")
    parser.add_argument("--api-base", default=None, help="Override API Base URL")
    parser.add_argument("--api-key", default=None, help="Override API Key")
    
    args = parser.parse_args()
    
    # Cấu hình API nếu có ghi đè
    from backend.modules import orchestrator as orch
    if args.provider:
        orch.current_api_config["provider"] = args.provider
    if args.model:
        orch.current_api_config["model"] = args.model
    if args.api_base:
        orch.current_api_config["api_base"] = args.api_base
    if args.api_key:
        orch.current_api_config["api_key"] = args.api_key
        
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"❌ ERROR: Input file not found: {args.input}")
        sys.exit(1)
        
    output_path = args.output
    if not output_path:
        output_path = input_path.parent / f"{input_path.stem}_translated.srt"
    else:
        output_path = Path(output_path)
        
    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()
        
    print(f"🚀 Starting translation to '{args.lang}' using {orch.current_api_config['provider']}...")
    translator = SubtitleTranslator()
    translated_content = asyncio.run(translator.translate_srt(content, target_lang=args.lang))
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(translated_content)
        
    print(f"🎉 SUCCESS: Subtitles translated successfully! Saved to: {output_path}")

