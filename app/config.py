from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    devin_api_key: str = ""
    devin_org_id: str = ""
    github_webhook_secret: str = ""
    target_repo: str = "emailsiwoo/superset-demo"
    trigger_label: str = "devin-autofix"

    devin_api_base: str = "https://api.devin.ai/v1"
    poll_interval_seconds: int = 60

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
