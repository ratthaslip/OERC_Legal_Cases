"""การตั้งค่าทั้งหมดอ่านจาก environment variable (ดู .env.example)"""
from functools import lru_cache
import os


class Settings:
    def __init__(self) -> None:
        self.database_url = os.getenv("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/erc")

        # SLA (SDD 5.1 ข)
        self.sla_default_days = int(os.getenv("SLA_DEFAULT_DAYS", "90"))
        self.sla_near_due_days = int(os.getenv("SLA_NEAR_DUE_DAYS", "14"))
        self.sla_day_count = os.getenv("SLA_DAY_COUNT", "calendar")  # calendar | business

        # Embedding: hash = ไม่ต้องใช้โมเดล (dev/test), openai_compat = Ollama / vLLM / TEI
        self.embed_provider = os.getenv("EMBED_PROVIDER", "hash")
        self.embed_base_url = os.getenv("EMBED_BASE_URL", "http://localhost:11434/v1")
        self.embed_model = os.getenv("EMBED_MODEL", "bge-m3")
        self.embed_api_key = os.getenv("EMBED_API_KEY", "")
        self.embed_dim = int(os.getenv("EMBED_DIM", "256"))  # ใช้กับ hash เท่านั้น

        # LLM: extractive = ไม่เรียกโมเดล (สาธิตแบบออฟไลน์), openai_compat = GPU ของตัวเองผ่าน Ollama/vLLM,
        # anthropic = cloud API (ใช้กับข้อมูลสมมติเท่านั้น)
        self.llm_provider = os.getenv("LLM_PROVIDER", "extractive")
        self.llm_base_url = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
        self.llm_model = os.getenv("LLM_MODEL", "qwen2.5:14b-instruct")
        self.llm_api_key = os.getenv("LLM_API_KEY", "")
        self.llm_timeout_s = float(os.getenv("LLM_TIMEOUT_S", "60"))

        # RAG (SDD 6.2)
        self.rag_top_k = int(os.getenv("RAG_TOP_K", "12"))
        # เกณฑ์หลักฐานขั้นต่ำ (ต้องปรับจากผล evaluation กับ golden set): สัดส่วนคำสำคัญของคำถามที่พบในแหล่ง
        # หรือ cosine ของ embedding (ค่า default เหมาะกับ bge-m3; hash embedding แทบไม่ผ่านเกณฑ์ cosine)
        self.rag_min_coverage = float(os.getenv("RAG_MIN_COVERAGE", "0.5"))
        self.rag_min_cosine = float(os.getenv("RAG_MIN_COSINE", "0.6"))
        self.assistant_enabled = os.getenv("ASSISTANT_ENABLED", "true").lower() == "true"

        self.cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")


@lru_cache
def get_settings() -> Settings:
    return Settings()
