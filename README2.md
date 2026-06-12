# 🎬 AI Video Subtitle & Translation Pipeline

Hệ thống AI tự động:

- Download video
- Tách audio
- Nhận diện giọng nói (ASR)
- Tạo phụ đề
- Dịch đa ngôn ngữ
- Xuất subtitle
- Tự động pipeline bằng Web UI

---

# ✨ Mục tiêu hệ thống

Dự án được thiết kế theo hướng:

✅ Modular Architecture  
✅ Mỗi module hoạt động độc lập  
✅ Có thể thay thế model dễ dàng  
✅ Có thể scale nhiều máy  
✅ Có Web UI để orchestration toàn bộ pipeline  
✅ Có API nội bộ giữa các module  
✅ Hỗ trợ queue/job system  
✅ Hỗ trợ local AI + cloud AI  

---

# 🧠 Kiến trúc tổng thể

```text
                ┌──────────────────┐
                │      WEB UI      │
                │ Pipeline Control │
                └────────┬─────────┘
                         │
                         ▼

┌───────────────────────────────────────────────┐
│               ORCHESTRATOR                    │
│         (Workflow / Queue Manager)            │
└──────┬──────────┬──────────┬──────────┬───────┘
       │          │          │          │
       ▼          ▼          ▼          ▼

┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│Downloader│ │ Audio    │ │ Subtitle │ │Translator│
│ Module   │ │ Extract  │ │ Module   │ │ Module   │
└──────────┘ └──────────┘ └──────────┘ └──────────┘

       ▼
┌──────────┐
│ Exporter │
└──────────┘