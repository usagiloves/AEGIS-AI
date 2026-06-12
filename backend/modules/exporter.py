import os
import time
import subprocess
import asyncio
import shutil
from pathlib import Path
from backend.config import OUTPUT_DIR, FFMPEG_USE_GPU, SUBTITLE_DEFAULT_STYLE

class SubtitleExporter:
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
            print(f"[SubtitleExporter] {message}")

    def wrap_text_vietnamese(self, text, max_chars=36):
        """
        Tự động ngắt dòng thông minh cho tiếng Việt dựa trên số ký tự tối đa trên một dòng,
        đảm bảo không bị vỡ từ ghép và cân đối giữa các dòng.
        """
        # Nếu câu đã chứa tag xuống dòng thủ công do người dùng tự gõ, tôn trọng giữ nguyên
        if "\\N" in text or "\n" in text:
            return text
            
        words = text.split()
        if not words:
            return ""
            
        lines = []
        current_line = []
        current_length = 0
        
        for word in words:
            word_len = len(word)
            addition = 1 if current_length > 0 else 0
            
            if current_length + addition + word_len > max_chars:
                if current_line:
                    lines.append(" ".join(current_line))
                    current_line = [word]
                    current_length = word_len
                else:
                    lines.append(word)
                    current_length = 0
            else:
                current_line.append(word)
                current_length += addition + word_len
                
        if current_line:
            lines.append(" ".join(current_line))
            
        # Trả về ký tự xuống dòng thực tế. FFmpeg subtitles filter sẽ render thành 2 dòng hoàn hảo!
        return "\n".join(lines)

    def auto_wrap_srt(self, srt_content, max_chars=36):
        """
        Đọc nội dung SRT và tự động ngắt dòng thông minh cho tất cả các block phụ đề.
        """
        try:
            blocks = srt_content.strip().replace('\r\n', '\n').split('\n\n')
            wrapped_blocks = []
            
            for block in blocks:
                lines = block.split('\n')
                if len(lines) >= 3:
                    index = lines[0].strip()
                    timeframe = lines[1].strip()
                    text = "\n".join(lines[2:]).strip()
                    
                    # Ngắt dòng thông minh cho phần text
                    wrapped_text = self.wrap_text_vietnamese(text, max_chars)
                    
                    wrapped_blocks.append(f"{index}\n{timeframe}\n{wrapped_text}")
                else:
                    wrapped_blocks.append(block)
                    
            return "\n\n".join(wrapped_blocks)
        except Exception as e:
            self._log(f"[Auto-Wrap] Gặp lỗi khi tự động căn dòng phụ đề: {str(e)}")
            return srt_content

    def merge_srt_dual(self, orig_srt, trans_srt):
        """
        Gộp hai tệp phụ đề (gốc và dịch) thành dạng phụ đề kép (Dual Subtitles).
        Dòng trên là dịch (to, rõ), dòng dưới là tiếng gốc (nhỏ hơn, màu xám nhẹ).
        """
        self._log("Đang tiến hành gộp phụ đề song ngữ...")
        try:
            def parse_srt(srt_text):
                blocks = srt_text.strip().replace('\r\n', '\n').split('\n\n')
                parsed = {}
                for block in blocks:
                    lines = block.split('\n')
                    if len(lines) >= 3:
                        timeframe = lines[1].strip()
                        text = " ".join(lines[2:]).strip()
                        parsed[timeframe] = text
                return parsed
                
            orig_dict = parse_srt(orig_srt)
            trans_dict = parse_srt(trans_srt)
            
            merged_lines = []
            idx = 1
            
            for timeframe, trans_text in trans_dict.items():
                orig_text = orig_dict.get(timeframe, "")
                if orig_text:
                    merged_text = f"{trans_text}\n<font color=\"#b0b0b0\">{orig_text}</font>"
                else:
                    merged_text = trans_text
                    
                merged_lines.append(f"{idx}\n{timeframe}\n{merged_text}\n")
                idx += 1
                
            return "\n".join(merged_lines)
        except Exception as e:
            self._log(f"Lỗi khi gộp song ngữ: {str(e)}. Sử dụng phụ đề dịch.")
            return trans_srt

    async def burn_subtitles(self, video_path, srt_path, output_filename=None, subtitle_style=None):
        """
        Nhúng phụ đề vào video bằng FFmpeg hỗ trợ FastStart và tự động ngắt dòng thông minh.
        """
        self._log("Đang tiến hành nhúng phụ đề vào video...")
        
        video_path = Path(video_path).resolve()
        srt_path = Path(srt_path).resolve()
        
        if not output_filename:
            base, ext = os.path.splitext(video_path.name)
            output_filename = f"{base}_subbed.mp4"
            
        output_path = OUTPUT_DIR / output_filename
        working_dir = srt_path.parent
        
        temp_srt_name = f"temp_burn_{int(time.time())}.srt"
        temp_srt_path = working_dir / temp_srt_name
        
        try:
            # 1. Đọc và tự động ngắt dòng phụ đề thông minh trước khi nhúng!
            with open(srt_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
            self._log("Đang tự động căn chỉnh và ngắt dòng phụ đề thông minh...")
            wrapped_content = self.auto_wrap_srt(content)
            
            with open(temp_srt_path, 'w', encoding='utf-8') as f:
                f.write(wrapped_content)
            
            rel_video = os.path.relpath(video_path, working_dir).replace('\\', '/')
            rel_srt = temp_srt_name
            rel_output = os.path.relpath(output_path, working_dir).replace('\\', '/')

            # Thiết lập style ASS cao cấp mặc định để chữ luôn đẹp và nổi bật
            style_str = subtitle_style or "FontSize=16,PrimaryColour=&H00FFFF,OutlineColour=&H000000,BorderStyle=1,Outline=2.5,Shadow=1.5,MarginV=25,Alignment=2,Fontname=Outfit"

            cmd_cpu = [
                'ffmpeg', '-y',
                '-i', rel_video,
                '-vf', f"subtitles={rel_srt}:force_style='{style_str}'",
                '-c:v', 'libx264',
                '-crf', '18',          # CRF 18 cho chất lượng hình ảnh sắc nét, gần như không nén (visually lossless)
                '-preset', 'medium',    # Preset medium cho độ nén tối ưu và chất lượng khung hình tốt hơn
                '-pix_fmt', 'yuv420p',  # Định dạng màu chuẩn tương thích 100% tất cả trình duyệt HTML5
                '-movflags', '+faststart',  # Cơ chế FastStart cho phép phát video streaming mượt mà tức thì
                '-c:a', 'copy',
                rel_output
            ]
            
            gpu_active = FFMPEG_USE_GPU
            cmd = cmd_cpu
            
            if gpu_active:
                self._log(f"Chuẩn bị nhúng phụ đề tăng tốc bằng GPU NVIDIA (h264_nvenc) chất lượng tối đa...")
                cmd = [
                    'ffmpeg', '-y',
                    '-i', rel_video,
                    '-vf', f"subtitles={rel_srt}:force_style='{style_str}'",
                    '-c:v', 'h264_nvenc',
                    '-preset', 'p7',    # Preset p7 - Tùy chọn chất lượng cao nhất của NVIDIA NVENC
                    '-rc', 'vbr',       # Rate control Variable Bitrate
                    '-cq', '19',        # Constant Quality 19 cho hình ảnh cực kỳ sắc nét
                    '-b:v', '0',        # Bắt buộc khi dùng Constant Quality với h264_nvenc
                    '-pix_fmt', 'yuv420p',  # Định dạng màu chuẩn tương thích 100% tất cả trình duyệt HTML5
                    '-movflags', '+faststart',  # FastStart
                    '-c:a', 'copy',
                    rel_output
                ]
                
            self._log("Đang thực thi FFmpeg...")
            
            def run_ffmpeg(command_to_run):
                return subprocess.run(
                    command_to_run, 
                    cwd=str(working_dir), 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE, 
                    text=True, 
                    encoding='utf-8',
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                )
                
            res = await asyncio.to_thread(run_ffmpeg, cmd)
            
            if res.returncode != 0 and gpu_active:
                self._log(f"Mã hóa GPU lỗi: {res.stderr.strip()[:150]}. Thử fallback về CPU...")
                res = await asyncio.to_thread(run_ffmpeg, cmd_cpu)
                
            if res.returncode == 0:
                self._log("Nhúng phụ đề thành công!")
                return {
                    "success": True,
                    "filepath": str(output_path)
                }
            else:
                self._log(f"Lỗi FFmpeg: {res.stderr}")
                return {
                    "success": False,
                    "error": res.stderr
                }
        except Exception as e:
            self._log(f"Không thể khởi chạy FFmpeg: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }
        finally:
            if temp_srt_path.exists():
                try:
                    os.remove(temp_srt_path)
                except Exception:
                    pass

if __name__ == "__main__":
    import argparse
    import sys
    
    # Đảm bảo UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding='utf-8')
        
    parser = argparse.ArgumentParser(description="AEGIS Subtitle Exporter CLI (FFmpeg-Powered Subtitle Burner)")
    parser.add_argument("-v", "--video", required=True, help="Path to input video file")
    parser.add_argument("-s", "--subtitle", required=True, help="Path to SRT subtitle file to burn (translated subtitle if dual-sub)")
    parser.add_argument("-o", "--output", default=None, help="Name of output video file (saved in output directory)")
    parser.add_argument("--style", default=None, help="FFmpeg subtitle custom style string")
    parser.add_argument("--dual", action="store_true", help="Enable Dual Bilingual Subtitles")
    parser.add_argument("-g", "--original-subtitle", default=None, help="Path to original (source language) SRT subtitle file for Dual Sub")
    
    args = parser.parse_args()
    
    exporter = SubtitleExporter()
    
    video_path = Path(args.video)
    srt_path = Path(args.subtitle)
    
    if not video_path.exists() or not srt_path.exists():
        print("❌ ERROR: Video file or Subtitle file does not exist.")
        sys.exit(1)
        
    if args.dual:
        if not args.original_subtitle:
            print("❌ ERROR: Dual subtitles require --original-subtitle (-g) path.")
            sys.exit(1)
        orig_path = Path(args.original_subtitle)
        if not orig_path.exists():
            print(f"❌ ERROR: Original subtitle file not found: {args.original_subtitle}")
            sys.exit(1)
            
        print("✨ Merging bilingual subtitles...")
        with open(orig_path, 'r', encoding='utf-8') as f:
            orig_content = f.read()
        with open(srt_path, 'r', encoding='utf-8') as f:
            trans_content = f.read()
            
        merged_content = exporter.merge_srt_dual(orig_content, trans_content)
        
        temp_dual_path = srt_path.parent / f"temp_cli_dual_{int(time.time())}.srt"
        with open(temp_dual_path, 'w', encoding='utf-8') as f:
            f.write(merged_content)
        srt_path = temp_dual_path
        
    print("🔥 Starting FFmpeg subtitle burn process...")
    res = asyncio.run(exporter.burn_subtitles(
        video_path,
        srt_path,
        output_filename=args.output,
        subtitle_style=args.style
    ))
    
    if args.dual and srt_path.name.startswith("temp_cli_dual_"):
        try:
            os.remove(srt_path)
        except Exception:
            pass
            
    if res.get("success"):
        print(f"🎉 SUCCESS: Subtitled video created successfully! Path: {res['filepath']}")
    else:
        print(f"❌ ERROR: FFmpeg burning failed. Detail: {res.get('error')}")
