import os
import sys
import asyncio
import subprocess
import traceback
from pathlib import Path
from backend.config import TEMP_DIR, OUTPUT_DIR, FFMPEG_USE_GPU

# Đảm bảo Edge-TTS có sẵn
try:
    import edge_tts
except ImportError:
    # Tự động cài đặt nếu chưa có
    subprocess.run([sys.executable, "-m", "pip", "install", "edge-tts"], stdout=subprocess.DEVNULL)
    import edge_tts

class VoiceoverGenerator:
    def __init__(self, progress_callback=None):
        self.progress_callback = progress_callback

    def log(self, message):
        if self.progress_callback:
            self.progress_callback(message)
        else:
            print(f"[Voiceover] {message}")

    def parse_srt(self, srt_path):
        """
        Đọc và phân tích file phụ đề SRT để lấy nội dung text lồng tiếng và timeline khớp.
        """
        segments = []
        if not os.path.exists(srt_path):
            return segments

        with open(srt_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()

        blocks = content.split('\n\n')
        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) >= 3:
                # time_line: 00:00:01,230 --> 00:00:03,450
                time_line = lines[1]
                text = " ".join(lines[2:])
                
                # Chuyển đổi dấu thời gian sang giây để so sánh hoặc khớp thời gian
                parts = time_line.split('-->')
                if len(parts) == 2:
                    start_str, end_str = parts[0].strip(), parts[1].strip()
                    start_sec = self.srt_time_to_seconds(start_str)
                    end_sec = self.srt_time_to_seconds(end_str)
                    
                    segments.append({
                        "start": start_sec,
                        "end": end_sec,
                        "text": text,
                        "start_str": start_str,
                        "end_str": end_str
                    })
        return segments

    def srt_time_to_seconds(self, time_str):
        time_str = time_str.replace(',', '.')
        parts = time_str.split(':')
        h, m, s = float(parts[0]), float(parts[1]), float(parts[2])
        return h * 3600 + m * 60 + s

    async def setup_piper(self):
        """
        Tự động tải Piper CLI zip cho Windows và các file model tiếng Việt vivos/tiếng Anh lessac
        về thư mục cục bộ backend/bin/piper/ nếu chưa có.
        """
        import urllib.request
        import zipfile
        
        bin_dir = Path("backend") / "bin" / "piper"
        bin_dir.mkdir(parents=True, exist_ok=True)
        
        piper_exe = bin_dir / "piper" / "piper.exe"
        
        # 1. Tải và giải nén Piper CLI
        if not piper_exe.exists():
            self.log("Không tìm thấy Piper CLI cục bộ. Bắt đầu tự động tải Piper cho Windows...")
            zip_url = "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_windows_amd64.zip"
            zip_path = bin_dir / "piper_windows.zip"
            
            try:
                def report(block_num, block_size, total_size):
                    read_so_far = block_num * block_size
                    if total_size > 0:
                        percent = min(100, read_so_far * 100 / total_size)
                        if block_num % 150 == 0:
                            self.log(f"Đang tải Piper zip: {percent:.1f}%")
                
                urllib.request.urlretrieve(zip_url, str(zip_path), reporthook=report)
                self.log("Tải Piper zip thành công! Đang giải nén...")
                
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(str(bin_dir))
                
                if zip_path.exists():
                    os.remove(zip_path)
                self.log("Đã giải nén Piper CLI thành công cục bộ!")
            except Exception as e:
                self.log(f"Lỗi tải/giải nén Piper CLI: {str(e)}")
                raise e

        # 2. Tải model tiếng Việt vivos mặc định
        model_name = "vi_VN-vivos-x_low"
        model_onnx = bin_dir / f"{model_name}.onnx"
        model_json = bin_dir / f"{model_name}.onnx.json"
        
        if not model_onnx.exists() or not model_json.exists():
            self.log("Thiếu model tiếng Việt Piper (vivos). Đang tự động tải từ HuggingFace...")
            onnx_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/vi/vi_VN/vivos/x_low/{model_name}.onnx"
            json_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/vi/vi_VN/vivos/x_low/{model_name}.onnx.json"
            
            try:
                self.log("Đang tải file model .onnx tiếng Việt...")
                urllib.request.urlretrieve(onnx_url, str(model_onnx))
                self.log("Đang tải file cấu hình .json tiếng Việt...")
                urllib.request.urlretrieve(json_url, str(model_json))
                self.log("Đã tải đầy đủ model tiếng Việt vivos thành công!")
            except Exception as e:
                self.log(f"Lỗi tải model tiếng Việt Piper: {str(e)}")
                raise e

    async def generate_edge(self, text, voice, output_path):
        """
        Sinh âm thanh bằng Edge-TTS (Nhanh, miễn phí).
        """
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)

    async def generate_openai(self, text, voice, api_key_openai, output_path):
        """
        Sinh âm thanh bằng OpenAI Cloud TTS API.
        """
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key_openai)
        
        openai_voices = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
        resolved_voice = voice if voice in openai_voices else "alloy"
        
        self.log(f"Đang gửi yêu cầu OpenAI TTS (voice: {resolved_voice})...")
        response = await client.audio.speech.create(
            model="tts-1",
            voice=resolved_voice,
            input=text
        )
        audio_content = await response.aread()
        with open(output_path, 'wb') as f:
            f.write(audio_content)

    async def generate_elevenlabs(self, text, voice, api_key_elevenlabs, emotion, output_path):
        """
        Sinh âm thanh bằng ElevenLabs Premium Cinematic REST API.
        """
        import httpx
        
        # ElevenLabs Voice ID Rachel làm mặc định nếu không khớp
        resolved_voice = voice if (voice and len(voice) > 10 and "-" not in voice) else "21m00Tcm4TlvDq8ikWAM"
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{resolved_voice}"
        
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": api_key_elevenlabs
        }
        
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.45,
                "similarity_boost": 0.75,
                "style": 0.6 if emotion == "cinematic" else 0.3,
                "use_speaker_boost": True
            }
        }
        
        self.log(f"Đang gửi yêu cầu ElevenLabs Premium TTS (voice id: {resolved_voice})...")
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                with open(output_path, 'wb') as f:
                    f.write(resp.content)
            else:
                try:
                    err_msg = resp.json()
                    detail = err_msg.get("detail", {}).get("message", resp.text)
                except Exception:
                    detail = resp.text
                raise RuntimeError(f"ElevenLabs API trả về mã {resp.status_code}: {detail}")

    async def generate_piper(self, text, voice, output_path):
        """
        Sinh âm thanh bằng Piper offline CLI cục bộ.
        """
        bin_dir = Path("backend") / "bin" / "piper"
        piper_exe = bin_dir / "piper" / "piper.exe"
        
        # Chuẩn hóa tên model
        piper_voice = "vi_VN-vivos-x_low"
        if voice == "en_US-lessac-medium" or "lessac" in voice:
            piper_voice = "en_US-lessac-medium"
            
        model_onnx = bin_dir / f"{piper_voice}.onnx"
        
        # Nếu thiếu model tiếng Anh, tự động tải
        if not model_onnx.exists() and piper_voice == "en_US-lessac-medium":
            import urllib.request
            self.log(f"Thiếu model tiếng Anh Piper ({piper_voice}). Đang tự động tải từ HuggingFace...")
            onnx_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/{piper_voice}.onnx"
            json_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/{piper_voice}.onnx.json"
            
            try:
                urllib.request.urlretrieve(onnx_url, str(model_onnx))
                urllib.request.urlretrieve(json_url, str(bin_dir / f"{piper_voice}.onnx.json"))
                self.log("Đã tải model tiếng Anh lessac thành công!")
            except Exception as e:
                self.log(f"Lỗi tải model tiếng Anh lessac: {str(e)}")
                raise e

        # Nếu output_path yêu cầu đuôi .mp3, ta sinh WAV tạm rồi convert sang MP3 bằng FFmpeg
        use_convert = output_path.lower().endswith(".mp3")
        temp_wav_path = output_path + ".wav" if use_convert else output_path

        # Chạy lệnh CLI Piper
        cmd = [
            str(piper_exe),
            "--model", str(model_onnx),
            "--output_file", str(temp_wav_path)
        ]
        
        self.log(f"Đang chạy Piper offline render cục bộ ({piper_voice})...")
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate(input=text.encode('utf-8'))
        
        if process.returncode == 0:
            self.log("Piper offline render thành công cục bộ!")
            
            if use_convert:
                self.log("Đang chuyển đổi tệp Piper WAV sang MP3 bằng FFmpeg...")
                convert_cmd = [
                    "ffmpeg", "-y",
                    "-i", str(temp_wav_path),
                    "-c:a", "libmp3lame",
                    str(output_path)
                ]
                conv_proc = await asyncio.create_subprocess_exec(
                    *convert_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await conv_proc.communicate()
                
                # Xóa file WAV tạm
                if os.path.exists(temp_wav_path):
                    try:
                        os.remove(temp_wav_path)
                    except Exception:
                        pass
                self.log("Chuyển đổi sang MP3 hoàn tất!")
        else:
            err_log = stderr.decode('utf-8', errors='ignore')
            raise RuntimeError(f"Lỗi Piper CLI: {err_log}")

    async def generate_tts(self, text, voice, output_path, engine="edge", emotion="neutral", api_key_openai="", api_key_elevenlabs=""):
        """
        Sinh file audio từ text sử dụng cơ chế chuyển mạch tự động (Fallback Routing).
        """
        # Xác định danh sách ưu tiên dựa theo cấu hình
        engines_order = []
        if engine == "elevenlabs":
            engines_order = ["elevenlabs", "openai", "edge", "piper"]
        elif engine == "openai":
            engines_order = ["openai", "edge", "piper"]
        elif engine == "piper":
            engines_order = ["piper", "edge"]
        elif engine == "auto":
            if api_key_elevenlabs:
                engines_order = ["elevenlabs", "openai", "edge", "piper"]
            elif api_key_openai:
                engines_order = ["openai", "edge", "piper"]
            else:
                engines_order = ["edge", "piper"]
        else: # "edge" hoặc mặc định
            engines_order = ["edge", "piper"]

        last_error = None
        for current_engine in engines_order:
            try:
                self.log(f"Thử sinh TTS bằng động cơ: {current_engine.upper()}...")
                
                if current_engine == "elevenlabs":
                    if not api_key_elevenlabs:
                        raise ValueError("Thiếu ElevenLabs API Key.")
                    await self.generate_elevenlabs(text, voice, api_key_elevenlabs, emotion, output_path)
                    return
                    
                elif current_engine == "openai":
                    if not api_key_openai:
                        raise ValueError("Thiếu OpenAI API Key.")
                    await self.generate_openai(text, voice, api_key_openai, output_path)
                    return
                    
                elif current_engine == "edge":
                    # Tránh truyền sai giọng đọc của cloud/piper vào edge-tts
                    edge_voice = voice if "Neural" in voice else "vi-VN-HoaiMyNeural"
                    await self.generate_edge(text, edge_voice, output_path)
                    return
                    
                elif current_engine == "piper":
                    # Setup Piper
                    await self.setup_piper()
                    piper_voice = "vi_VN-vivos-x_low"
                    if "lessac" in voice or voice == "en_US-lessac-medium":
                        piper_voice = "en_US-lessac-medium"
                    await self.generate_piper(text, piper_voice, output_path)
                    return
                    
            except Exception as e:
                self.log(f"⚠️ [Warning] Thất bại khi lồng tiếng bằng {current_engine.upper()}: {str(e)}")
                last_error = e
                self.log("Đang tự động chuyển mạch sang Engine dự phòng...")

        raise RuntimeError(f"Tất cả các TTS Engine đều thất bại. Lỗi cuối cùng: {str(last_error)}")

    async def dub_video(self, video_path, srt_path, voice="vi-VN-HoaiMyNeural", mix_ratio=0.7, engine="edge", emotion="neutral", api_key_openai="", api_key_elevenlabs=""):
        """
        Quy trình lồng tiếng AI hoàn chỉnh hỗ trợ Intelligent Voice Router
        """
        try:
            self.log(f"Bắt đầu quy trình lồng tiếng AI (Engine: {engine.upper()})...")
            segments = self.parse_srt(srt_path)
            
            if not segments:
                self.log("Không tìm thấy phụ đề nào trong file SRT để lồng tiếng.")
                return {"success": False, "error": "SRT file is empty or missing"}

            # Chuẩn hóa mix_ratio từ client gửi lên (VD: 70% -> 0.7)
            normalized_mix = float(mix_ratio)
            if normalized_mix > 1.0:
                normalized_mix = normalized_mix / 100.0
            
            # Gom toàn bộ văn bản để sinh một file tiếng TTS tổng thể (hoặc nối theo timeline)
            full_text = " . ".join([seg["text"] for seg in segments])
            
            temp_tts_path = Path(TEMP_DIR) / f"tts_{int(asyncio.get_event_loop().time())}.mp3"
            self.log("Đang tổng hợp giọng nói AI (Text-to-Speech)...")
            
            await self.generate_tts(
                full_text, voice, str(temp_tts_path),
                engine=engine, emotion=emotion,
                api_key_openai=api_key_openai,
                api_key_elevenlabs=api_key_elevenlabs
            )
            
            self.log("Tổng hợp giọng đọc AI thành công!")

            # Ghép audio vào video bằng FFmpeg
            output_filename = f"dubbed_{Path(video_path).name}"
            final_output_path = Path(OUTPUT_DIR) / output_filename
            
            self.log("Đang hòa âm và kết xuất video lồng tiếng (FFmpeg)...")
            
            original_weight = 1.0 - normalized_mix
            
            cmd = [
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-i", str(temp_tts_path),
                "-filter_complex", f"[0:a]volume={original_weight:.2f}[a0];[1:a]volume={normalized_mix:.2f}[a1];[a0][a1]amix=inputs=2:duration=first[aout]",
                "-map", "0:v",
                "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac",
                "-movflags", "+faststart",  # Tối ưu hóa FastStart phát streaming mượt mà tức thì
                "-shortest",
                str(final_output_path)
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                self.log("Lồng tiếng video thành công!")
                # Xóa tệp tts tạm
                if temp_tts_path.exists():
                    try:
                        os.remove(temp_tts_path)
                    except Exception:
                        pass
                return {"success": True, "filepath": str(final_output_path)}
            else:
                error_log = stderr.decode('utf-8', errors='ignore')
                self.log(f"Lỗi FFmpeg khi lồng tiếng: {error_log}")
                return {"success": False, "error": error_log}

        except Exception as e:
            traceback.print_exc()
            self.log(f"Gặp sự cố khi lồng tiếng: {str(e)}")
            return {"success": False, "error": str(e)}
