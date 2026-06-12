import os
import time
import subprocess
import asyncio
from pathlib import Path

class AudioExtractor:
    def __init__(self, progress_callback=None):
        self.progress_callback = progress_callback

    def _log(self, message):
        if self.progress_callback:
            # Check if callback is async
            if asyncio.iscoroutinefunction(self.progress_callback):
                try:
                    loop = asyncio.get_running_loop()
                    asyncio.run_coroutine_threadsafe(self.progress_callback({"status": "info", "message": message}), loop)
                except RuntimeError:
                    asyncio.run(self.progress_callback({"status": "info", "message": message}))
            else:
                self.progress_callback({"status": "info", "message": message})
        else:
            print(f"[AudioExtractor] {message}")

    async def extract_audio(self, video_path, output_audio_path=None):
        """
        Trích xuất âm thanh từ video sang tệp WAV thô.
        """
        video_path = Path(video_path).resolve()
        if not output_audio_path:
            output_audio_path = video_path.parent / f"extracted_{int(time.time())}.wav"
        else:
            output_audio_path = Path(output_audio_path).resolve()

        self._log(f"Đang trích xuất âm thanh từ video: {video_path.name}...")
        
        cmd = [
            'ffmpeg', '-y',
            '-i', str(video_path),
            '-vn',
            '-acodec', 'pcm_s16le',
            '-ar', '16000',
            '-ac', '1',
            str(output_audio_path)
        ]

        def run():
            return subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )

        res = await asyncio.to_thread(run)
        if res.returncode == 0:
            self._log("Trích xuất âm thanh hoàn tất!")
            return output_audio_path
        else:
            self._log(f"Lỗi trích xuất: {res.stderr.decode('utf-8', errors='ignore')}")
            return None

    async def enhance_audio(self, input_path, output_path=None):
        """
        Khử nhiễu và tăng cường giọng nói bằng bộ lọc FFmpeg (highpass, lowpass, afftdn).
        """
        self._log("Đang khử nhiễu và tăng cường chất lượng âm thanh (AI Audio Enhancement)...")
        input_path = Path(input_path).resolve()
        if not output_path:
            output_path = input_path.parent / f"enhanced_{int(time.time())}.wav"
        else:
            output_path = Path(output_path).resolve()

        filter_str = "afftdn,highpass=f=80,lowpass=f=8000,volume=1.3"
        
        cmd = [
            'ffmpeg', '-y',
            '-i', str(input_path),
            '-af', filter_str,
            '-vn',
            '-acodec', 'pcm_s16le',
            '-ar', '16000',
            '-ac', '1',
            str(output_path)
        ]
        
        def run():
            return subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            
        res = await asyncio.to_thread(run)
        if res.returncode == 0:
            self._log("Tăng cường âm thanh hoàn tất!")
            return output_path
        else:
            self._log(f"Lỗi tăng cường âm thanh: {res.stderr.strip()[:100]}")
            return None

if __name__ == "__main__":
    import argparse
    import sys
    
    # Đảm bảo UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding='utf-8')
        
    parser = argparse.ArgumentParser(description="AEGIS Audio Extractor & Enhancer CLI")
    parser.add_argument("-i", "--input", required=True, help="Path to input video/audio file")
    parser.add_argument("-o", "--output", default=None, help="Path to output WAV file")
    parser.add_argument("--enhance", action="store_true", help="Enhance audio with ASR cleaning filters (afftdn, highpass, lowpass)")
    
    args = parser.parse_args()
    
    extractor = AudioExtractor()
    if args.enhance:
        res = asyncio.run(extractor.enhance_audio(args.input, args.output))
    else:
        res = asyncio.run(extractor.extract_audio(args.input, args.output))
        
    if res:
        print(f"🎉 SUCCESS: Audio processed successfully! Output: {res}")
    else:
        print("❌ ERROR: Failed to process audio.")

