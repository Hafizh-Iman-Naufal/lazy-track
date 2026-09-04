from pathlib import Path

import tomllib
from dotenv import dotenv_values
from pydantic import BaseModel, Field, field_validator


class WorkOvertimeConfig(BaseModel):
    enabled: bool = True
    default_hours: float = 0


class WorkConfig(BaseModel):
    hours_per_day: float = 8.0
    weekly_target: float = 40.0
    working_days: list[str] = ["mon", "tue", "wed", "thu", "fri"]
    weekend_days: list[str] = ["sat", "sun"]
    timezone: str = "UTC"
    overtime: WorkOvertimeConfig = Field(default_factory=WorkOvertimeConfig)

    @field_validator("hours_per_day", "weekly_target")
    @classmethod
    def validate_positive(cls, v, info):
        if v <= 0:
            raise ValueError(f"{info.field_name} must be positive")
        return v

    @field_validator("working_days", "weekend_days")
    @classmethod
    def validate_days(cls, v):
        valid_days = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
        for day in v:
            if day.lower() not in valid_days:
                raise ValueError(f"Invalid day: {day}")
        return [d.lower() for d in v]


class JiraConfig(BaseModel):
    base_url: str = ""
    email: str = ""
    api_token: str = ""
    project: str = ""
    include_done: bool = False
    assigned_only: bool = True
    edit_manual_worklogs: bool = False


class AIProviderConfig(BaseModel):
    enabled: bool = True
    api_key: str = ""


class AIProvidersConfig(BaseModel):
    gemini: AIProviderConfig = Field(default_factory=AIProviderConfig)
    deepseek: AIProviderConfig = Field(default_factory=AIProviderConfig)
    minimax: AIProviderConfig = Field(default_factory=AIProviderConfig)


class AIConfig(BaseModel):
    provider: str = "gemini"
    model: str = ""
    providers: AIProvidersConfig = Field(default_factory=AIProvidersConfig)


class SafetyConfig(BaseModel):
    require_confirmation: bool = True
    allow_issue_mutations: bool = False
    allow_worklog_create: bool = True
    allow_worklog_update: bool = True
    allow_worklog_delete: bool = True


class LazyTrackConfig(BaseModel):
    work: WorkConfig = Field(default_factory=WorkConfig)
    jira: JiraConfig = Field(default_factory=JiraConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)


def _load_toml_config(path: Path) -> dict:
    if path.exists():
        with open(path, "rb") as f:
            return tomllib.load(f)
    return {}


def _merge_dicts(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def load_config(
    config_path: Path | None = None,
    env_file_path: Path | None = None,
) -> LazyTrackConfig:
    defaults = LazyTrackConfig().model_dump()

    toml_config = {}
    if config_path is None:
        config_path = Path.cwd() / "config.toml"
    if config_path.exists():
        toml_config = _load_toml_config(config_path)

    if env_file_path is None:
        env_file_path = Path.cwd() / ".env"

    settings_data = {}
    if env_file_path.exists():
        settings_data = dotenv_values(env_file_path) if env_file_path.exists() else {}

    merged = _merge_dicts(defaults, toml_config)

    config = LazyTrackConfig(**merged)

    if settings_data.get("JIRA_BASE_URL"):
        config.jira.base_url = settings_data["JIRA_BASE_URL"]
    if settings_data.get("JIRA_EMAIL"):
        config.jira.email = settings_data["JIRA_EMAIL"]
    if settings_data.get("JIRA_API_TOKEN"):
        config.jira.api_token = settings_data["JIRA_API_TOKEN"]
    if settings_data.get("JIRA_PROJECT_KEY"):
        config.jira.project = settings_data["JIRA_PROJECT_KEY"]
    if settings_data.get("GEMINI_API_KEY"):
        config.ai.providers.gemini.api_key = settings_data["GEMINI_API_KEY"]
    if settings_data.get("DEEPSEEK_API_KEY"):
        config.ai.providers.deepseek.api_key = settings_data["DEEPSEEK_API_KEY"]
    if settings_data.get("MINIMAX_API_KEY"):
        config.ai.providers.minimax.api_key = settings_data["MINIMAX_API_KEY"]

    return config


def redact_secrets(config: LazyTrackConfig) -> dict:
    data = config.model_dump()

    data["jira"]["api_token"] = "[REDACTED]" if config.jira.api_token else None
    data["jira"]["email"] = "[REDACTED]" if config.jira.email else None

    providers = data.get("ai", {}).get("providers", {})
    for provider in ["gemini", "deepseek", "minimax"]:
        if provider in providers:
            api_key = getattr(config.ai.providers, provider).api_key
            providers[provider]["api_key"] = "[REDACTED]" if api_key else None

    return data
