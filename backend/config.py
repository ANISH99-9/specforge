"""
SpecForge — Configuration
Reads from .env and provides typed settings with model-tiering defaults.
"""
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # API Keys
    google_ai_api_key: str = Field("", alias="GOOGLE_AI_API_KEY")
    groq_api_key: str = Field("", alias="GROQ_API_KEY")

    # Model tier chain (Primary → Fallback → Emergency)
    primary_provider: str = Field("google", alias="PRIMARY_PROVIDER")
    primary_model: str = Field("gemini-2.5-flash", alias="PRIMARY_MODEL")

    fallback_provider: str = Field("groq", alias="FALLBACK_PROVIDER")
    fallback_model: str = Field("llama-3.1-8b-instant", alias="FALLBACK_MODEL")

    emergency_provider: str = Field("groq", alias="EMERGENCY_PROVIDER")
    emergency_model: str = Field("llama-3.1-8b-instant", alias="EMERGENCY_MODEL")

    # Per-stage overrides (all default to Primary: Gemini 2.5 Flash)
    stage1_provider: str = Field("google", alias="STAGE1_PROVIDER")
    stage1_model: str = Field("gemini-2.5-flash", alias="STAGE1_MODEL")

    stage2_provider: str = Field("google", alias="STAGE2_PROVIDER")
    stage2_model: str = Field("gemini-2.5-flash", alias="STAGE2_MODEL")

    stage3_provider: str = Field("google", alias="STAGE3_PROVIDER")
    stage3_model: str = Field("gemini-2.5-flash", alias="STAGE3_MODEL")

    stage4_provider: str = Field("google", alias="STAGE4_PROVIDER")
    stage4_model: str = Field("gemini-2.5-flash", alias="STAGE4_MODEL")

    repair_provider: str = Field("google", alias="REPAIR_PROVIDER")
    repair_model: str = Field("gemini-2.5-flash", alias="REPAIR_MODEL")

    # Pipeline
    max_repair_retries: int = Field(3, alias="MAX_REPAIR_RETRIES")
    pipeline_timeout_seconds: int = Field(120, alias="PIPELINE_TIMEOUT_SECONDS")

    # Server
    host: str = Field("0.0.0.0", alias="HOST")
    port: int = Field(8000, alias="PORT")
    cors_origins: str = Field(
        "http://localhost:5173,http://localhost:3000",
        alias="CORS_ORIGINS",
    )

    # Eval
    eval_db_path: str = Field("./eval/eval.db", alias="EVAL_DB_PATH")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]

    @property
    def model_tier_chain(self) -> list[tuple[str, str]]:
        """Primary → Fallback → Emergency provider/model pairs."""
        return [
            (self.primary_provider, self.primary_model),
            (self.fallback_provider, self.fallback_model),
            (self.emergency_provider, self.emergency_model),
        ]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore", "populate_by_name": True}


# Cost table ($ per 1M tokens, input / output)
MODEL_COSTS: dict[str, dict] = {
    "llama-3.3-70b-versatile": {"input": 0.59, "output": 0.79},
    "llama-3.1-8b-instant": {"input": 0.05, "output": 0.08},
    "gemini-2.0-flash": {"input": 0.075, "output": 0.30},
    "gemini-2.0-flash-001": {"input": 0.075, "output": 0.30},
    "gemini-2.5-flash": {"input": 0.075, "output": 0.30},
    "google/gemini-2.0-flash-001": {"input": 0.075, "output": 0.30},
    "google/gemini-2.5-flash": {"input": 0.075, "output": 0.30},
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    entry = MODEL_COSTS.get(model, {"input": 1.0, "output": 1.0})
    return (input_tokens * entry["input"] + output_tokens * entry["output"]) / 1_000_000


settings = Settings()
