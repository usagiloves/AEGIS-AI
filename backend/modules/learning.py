import os
import json
import time
import uuid
from pathlib import Path
from backend.config import MEMORIES_DIR

class LearningLoop:
    def __init__(self):
        self.db_path = MEMORIES_DIR / "memories.json"
        self.chroma_collection = None
        self._init_db()

    def _init_db(self):
        """
        Khởi tạo cơ sở dữ liệu trí nhớ. 
        Thử nghiệm sử dụng ChromaDB. Nếu môi trường Windows thiếu thư viện C++ hoặc lỗi cài đặt,
        tự động chuyển sang cơ chế Cơ sở dữ liệu JSON File + AI-powered Semantic Retrieval siêu bền bỉ.
        """
        try:
            import chromadb
            from chromadb.config import Settings
            
            # Khởi tạo client ChromaDB lưu trữ local
            client = chromadb.PersistentClient(path=str(MEMORIES_DIR))
            self.chroma_collection = client.get_or_create_collection(
                name="ai_employee_memories"
            )
            print("[LearningLoop] Đã khởi tạo bộ nhớ vector ChromaDB thành công.")
        except Exception as e:
            print(f"[LearningLoop] ChromaDB không khả dụng (Lỗi: {e}). Tự động chuyển sang chế độ lưu trữ JSON File dự phòng.")
            # Tạo file JSON nếu chưa tồn tại
            if not self.db_path.exists():
                with open(self.db_path, 'w', encoding='utf-8') as f:
                    json.dump([], f, ensure_ascii=False, indent=2)

    def save_memory(self, goal: str, plan_steps: list, success: bool, error_msg: str = ""):
        """
        Lưu kết quả thực hiện mục tiêu để làm bài học kinh nghiệm cho lần sau.
        """
        timestamp = time.time()
        memory_id = str(uuid.uuid4())
        
        # Tạo văn bản tổng kết bài học kinh nghiệm
        steps_summary = []
        for i, step in enumerate(plan_steps, 1):
            status_text = "Thành công" if step.get('status') == 'completed' else "Thất bại"
            steps_summary.append(f"Bước {i}: {step['title']} ({step['module']}.{step['action']}) - {status_text}. Kết quả: {step.get('result', '')}")
            
        summary_text = (
            f"Mục tiêu lớn: {goal}\n"
            f"Kết quả chung: {'Thành công tốt đẹp' if success else 'Thất bại tại bước ' + str(len(steps_summary))}\n"
            f"Chi tiết các bước thực hiện:\n" + "\n".join(steps_summary) + "\n"
        )
        
        if not success and error_msg:
            summary_text += f"Lỗi gặp phải: {error_msg}\nBài học kinh nghiệm rút ra: Khi thực hiện '{goal}', nếu gặp lỗi '{error_msg}', cần kiểm tra lại các tham số đầu vào và cấu hình an toàn hệ thống."

        # 1. Lưu bằng ChromaDB nếu có
        if self.chroma_collection:
            try:
                self.chroma_collection.add(
                    documents=[summary_text],
                    metadatas=[{"goal": goal, "success": str(success), "timestamp": timestamp}],
                    ids=[memory_id]
                )
                print("[LearningLoop] Đã lưu trí nhớ mới vào bộ nhớ vector ChromaDB.")
                return
            except Exception as e:
                print(f"[LearningLoop] Lỗi khi ghi vào ChromaDB: {e}. Đang ghi vào file JSON dự phòng.")

        # 2. Ghi vào file JSON dự phòng
        try:
            memories = []
            if self.db_path.exists():
                with open(self.db_path, 'r', encoding='utf-8') as f:
                    memories = json.load(f)
            
            memories.append({
                "id": memory_id,
                "goal": goal,
                "success": success,
                "summary": summary_text,
                "timestamp": timestamp
            })
            
            with open(self.db_path, 'w', encoding='utf-8') as f:
                json.dump(memories, f, ensure_ascii=False, indent=2)
            print("[LearningLoop] Đã ghi nhận bài học kinh nghiệm vào file memories.json.")
        except Exception as ex:
            print(f"[LearningLoop] Thất bại khi ghi file nhớ dự phòng: {ex}")

    def search_memories(self, query: str, limit: int = 3) -> str:
        """
        Tìm kiếm các bài học kinh nghiệm liên quan đến Goal hiện tại.
        Trả về chuỗi tổng hợp các bài học kinh nghiệm phù hợp nhất.
        """
        # 1. Tra cứu bằng ChromaDB nếu có
        if self.chroma_collection:
            try:
                results = self.chroma_collection.query(
                    query_texts=[query],
                    n_results=limit
                )
                if results and results['documents'] and results['documents'][0]:
                    docs = results['documents'][0]
                    print(f"[LearningLoop] ChromaDB tìm thấy {len(docs)} bài học liên quan.")
                    return "\n\n=== BÀI HỌC KINH NGHIỆM TRƯỚC ĐÂY ===\n" + "\n---\n".join(docs)
                return ""
            except Exception as e:
                print(f"[LearningLoop] Lỗi tra cứu ChromaDB: {e}. Chuyển sang tìm kiếm trên file JSON.")

        # 2. Tra cứu từ file JSON dự phòng (Sử dụng tìm kiếm từ khóa thông minh)
        try:
            if not self.db_path.exists():
                return ""
                
            with open(self.db_path, 'r', encoding='utf-8') as f:
                memories = json.load(f)
                
            if not memories:
                return ""
            
            # Phân tách từ khóa từ query để tính điểm khớp tương tự đơn giản
            query_words = set(query.lower().split())
            scored_memories = []
            
            for mem in memories:
                score = 0
                goal_lower = mem['goal'].lower()
                summary_lower = mem['summary'].lower()
                
                # Điểm cộng lớn nếu trùng từ trong Goal chính
                for word in query_words:
                    if len(word) > 2: # Bỏ qua các từ nối quá ngắn
                        if word in goal_lower:
                            score += 5
                        if word in summary_lower:
                            score += 1
                
                if score > 0:
                    scored_memories.append((score, mem))
            
            # Sắp xếp theo điểm tương đồng giảm dần
            scored_memories.sort(key=lambda x: x[0], reverse=True)
            matched_mems = [item[1]['summary'] for item in scored_memories[:limit]]
            
            if matched_mems:
                print(f"[LearningLoop] Tìm thấy {len(matched_mems)} bài học từ file memories.json.")
                return "\n\n=== BÀI HỌC KINH NGHIỆM TRƯỚC ĐÂY ===\n" + "\n---\n".join(matched_mems)
            
        except Exception as e:
            print(f"[LearningLoop] Lỗi tra cứu file nhớ dự phòng: {e}")
            
        return ""
