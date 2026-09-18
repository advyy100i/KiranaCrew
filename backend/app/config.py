from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")
    app_env: str = "local"
    database_url: str = "postgresql+psycopg://kirana:kirana@127.0.0.1:5433/kirana"
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    admin_api_key: str = ""
    jwt_secret: str = "dev"
    # STT
    stt_provider: str = "faster_whisper"          # faster_whisper | groq | fixture
    whisper_model: str = "large-v3-turbo"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_download_root: str | None = None
    max_voice_seconds: int = 60
    min_voice_seconds: int = 1
    max_voice_bytes: int = 20 * 1024 * 1024
    stt_min_avg_logprob: float = -1.0            # below this => CONFIRM_TRANSCRIPT
    stt_max_no_speech_prob: float = 0.6
    # Parser / LLM
    parser_mode: str = "hybrid"                   # rules | llm | hybrid
    llm_providers: str = "ollama,groq,gemini"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:3b"
    ollama_num_gpu: int = -1                      # -1 = Ollama decides; 0 = CPU only (GPU cannot pin memory)
    ollama_num_gpu: int | None = None            # 0 = CPU only (needed when pinned GPU memory is scarce)
    groq_api_key: str = ""
    groq_stt_model: str = "whisper-large-v3-turbo"
    groq_llm_model: str = "qwen/qwen3.8-27b"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash-lite"
    # Worker
    worker_concurrency: int = 2
    run_worker_inline: bool = False
    pending_ttl_minutes: int = 24 * 60
    undo_window_minutes: int = 15
    dashboard_origin: str = "*"

    @property
    def llm_provider_list(self) -> list[str]:
        return [p.strip() for p in self.llm_providers.split(",") if p.strip()]


settings = Settings()
