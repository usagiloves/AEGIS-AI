import os
import asyncio
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.modules.downloader import VideoDownloader
from backend.modules.audio import AudioExtractor
from backend.modules.subtitle import SubtitleEngine
from backend.modules.translator import SubtitleTranslator
from backend.modules.exporter import SubtitleExporter

router = APIRouter(prefix="/api/modules", tags=["Internal Modules API"])

# --- Models Định Nghĩa Dữ Liệu Yêu Cầu ---

class DownloadRequest(BaseModel):
    url: str
    quality: Optional[str] = "best"

class AudioEnhanceRequest(BaseModel):
    video_path: str
    output_path: Optional[str] = None

class TranscribeRequest(BaseModel):
    media_path: str
    offset: Optional[float] = 0.0

class TranslateRequest(BaseModel):
    srt_content: str
    target_lang: Optional[str] = "Tiếng Việt"

class BurnRequest(BaseModel):
    video_path: str
    srt_path: str
    subtitle_style: Optional[str] = None

class MergeRequest(BaseModel):
    orig_srt: str
    trans_srt: str

# --- Endpoints Cho Các Module Độc Lập ---

@router.post("/downloader/download")
async def api_downloader_download(req: DownloadRequest):
    """
    Tải video từ YouTube hoặc các nguồn URL khác.
    """
    # Ghi nhận chất lượng vào môi trường để downloader.py đọc được
    os.environ["DOWNLOAD_QUALITY"] = req.quality or "best"
    
    # Callback tiến trình rỗng cho API (chỉ log ra console)
    dl = VideoDownloader(progress_callback=None)
    try:
        res = await asyncio.to_thread(dl.download, req.url, quality=req.quality)
        if not res.get("success"):
            raise HTTPException(status_code=400, detail=res.get("error", "Lỗi tải video."))
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/audio/enhance")
async def api_audio_enhance(req: AudioEnhanceRequest):
    """
    Tách âm thanh từ video, khử nhiễu và tăng cường giọng nói.
    """
    extractor = AudioExtractor(progress_callback=None)
    try:
        enhanced_audio = await extractor.enhance_audio(req.video_path, req.output_path)
        if not enhanced_audio or not os.path.exists(enhanced_audio):
            raise HTTPException(status_code=500, detail="Không thể trích xuất và khử nhiễu âm thanh.")
        return {
            "success": True,
            "filepath": str(enhanced_audio)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/subtitle/transcribe")
async def api_subtitle_transcribe(req: TranscribeRequest):
    """
    Nhận dạng giọng nói (ASR) bằng faster-whisper và tạo tệp phụ đề gốc.
    """
    engine = SubtitleEngine(progress_callback=None)
    try:
        srt_content, lang = await engine.transcribe(req.media_path, offset=req.offset)
        return {
            "success": True,
            "srt_content": srt_content,
            "language": lang
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/translator/translate")
async def api_translator_translate(req: TranslateRequest):
    """
    Biên dịch tệp phụ đề SRT sang ngôn ngữ mục tiêu bằng LLM.
    """
    translator = SubtitleTranslator(progress_callback=None)
    try:
        translated_srt = await translator.translate_srt(req.srt_content, target_lang=req.target_lang)
        return {
            "success": True,
            "srt_content": translated_srt
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/exporter/burn")
async def api_exporter_burn(req: BurnRequest):
    """
    Nhúng phụ đề cứng vào video bằng FFmpeg.
    """
    exporter = SubtitleExporter(progress_callback=None)
    try:
        res = await exporter.burn_subtitles(
            req.video_path,
            req.srt_path,
            subtitle_style=req.subtitle_style
        )
        if not res.get("success"):
            raise HTTPException(status_code=500, detail=res.get("error", "Lỗi nhúng phụ đề."))
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/exporter/merge")
async def api_exporter_merge(req: MergeRequest):
    """
    Gộp hai tệp phụ đề (gốc và dịch) thành phụ đề song ngữ (Dual Subtitles).
    """
    exporter = SubtitleExporter(progress_callback=None)
    try:
        merged_srt = exporter.merge_srt_dual(req.orig_srt, req.trans_srt)
        return {
            "success": True,
            "srt_content": merged_srt
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
