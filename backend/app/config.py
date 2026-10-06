"""App settings, loaded from environment variables and backend/.env."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_mock: bool = True

    data_dir: Path = Path("./data")

    @property
    def data_path(self) -> Path:
        """data_dir as an absolute path; relative values are resolved against backend/."""
        return self.data_dir if self.data_dir.is_absolute() else BACKEND_DIR / self.data_dir


settings = Settings()
