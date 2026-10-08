"""App settings, loaded from environment variables and backend/.env."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    # Gemini's free tier through its OpenAI-compatible endpoint (PLAN.md section 4). Any other
    # OpenAI-compatible provider works too: change these three in .env.
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    llm_api_key: str = ""
    llm_model: str = "gemini-3.5-flash-lite"
    llm_mock: bool = True
    # Longer paper texts are cut to this many characters before they go into a prompt (~4 characters
    # per token, so 200K characters is ~50K tokens: almost every paper, with room left in a 128K context).
    llm_max_paper_chars: int = 200_000

    data_dir: Path = Path("./data")

    @property
    def data_path(self) -> Path:
        """data_dir as an absolute path; relative values are resolved against backend/."""
        return self.data_dir if self.data_dir.is_absolute() else BACKEND_DIR / self.data_dir


settings = Settings()
