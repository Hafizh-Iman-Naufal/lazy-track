import pytest
from pydantic import ValidationError

from lazytrack.config import (
    LazyTrackConfig,
    WorkConfig,
    JiraConfig,
    AIConfig,
    SafetyConfig,
    load_config,
    redact_secrets,
)


class TestWorkConfig:
    def test_default_config(self):
        config = WorkConfig()
        assert config.hours_per_day == 8.0
        assert config.weekly_target == 40.0
        assert config.working_days == ["mon", "tue", "wed", "thu", "fri"]
        assert config.weekend_days == ["sat", "sun"]

    def test_custom_hours(self):
        config = WorkConfig(hours_per_day=7.5, weekly_target=37.5)
        assert config.hours_per_day == 7.5
        assert config.weekly_target == 37.5

    def test_invalid_negative_hours(self):
        with pytest.raises(ValidationError):
            WorkConfig(hours_per_day=-1)

    def test_invalid_working_days(self):
        with pytest.raises(ValidationError):
            WorkConfig(working_days=["monday"])  # Full name not supported

    def test_invalid_zero_hours(self):
        with pytest.raises(ValidationError):
            WorkConfig(hours_per_day=0)

    def test_working_days_normalized_to_lowercase(self):
        config = WorkConfig(working_days=["MON", "TUE"])
        assert config.working_days == ["mon", "tue"]

    def test_default_day_start(self):
        config = WorkConfig()
        assert config.day_start == "10:00"

    def test_day_start_normalized(self):
        config = WorkConfig(day_start="9:05")
        assert config.day_start == "09:05"

    def test_invalid_day_start_hour(self):
        with pytest.raises(ValidationError):
            WorkConfig(day_start="25:00")

    def test_invalid_day_start_text(self):
        with pytest.raises(ValidationError):
            WorkConfig(day_start="noon")


class TestJiraConfig:
    def test_default_jira_config(self):
        config = JiraConfig()
        assert config.base_url == ""
        assert config.assigned_only is True
        assert config.edit_manual_worklogs is False


class TestAIConfig:
    def test_default_ai_config(self):
        config = AIConfig()
        assert config.provider == "gemini"
        assert config.model == ""


class TestSafetyConfig:
    def test_default_safety_config(self):
        config = SafetyConfig()
        assert config.require_confirmation is True
        assert config.allow_issue_mutations is False
        assert config.allow_worklog_create is True
        assert config.allow_worklog_update is True
        assert config.allow_worklog_delete is True
        assert config.worklog_lookback_weeks == 4
        assert config.max_plan_span_days == 14
        assert config.max_delete_ops_per_plan == 20

    def test_lookback_weeks_clamped(self):
        with pytest.raises(ValidationError):
            SafetyConfig(worklog_lookback_weeks=52)
        with pytest.raises(ValidationError):
            SafetyConfig(worklog_lookback_weeks=0)


class TestLazyTrackConfig:
    def test_full_config_defaults(self):
        config = LazyTrackConfig()
        assert config.work.hours_per_day == 8.0
        assert config.jira.assigned_only is True
        assert config.ai.provider == "gemini"
        assert config.safety.require_confirmation is True

    def test_config_with_custom_values(self):
        config = LazyTrackConfig(
            work={"hours_per_day": 6.0, "weekly_target": 30.0},
            jira={"base_url": "https://example.atlassian.net"},
        )
        assert config.work.hours_per_day == 6.0
        assert config.work.weekly_target == 30.0
        assert config.jira.base_url == "https://example.atlassian.net"


class TestLoadConfig:
    def test_load_config_returns_valid_config(self):
        config = load_config()
        assert isinstance(config, LazyTrackConfig)

    def test_load_config_with_missing_files(self):
        config = load_config(config_path=None, env_file_path=None)
        assert config.work.hours_per_day == 8.0

    def test_new_provider_keys_from_env(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text(
            "OPENAI_API_KEY=sk-test\n"
            "ANTHROPIC_API_KEY=ant-test\n"
            "OPENCODE_API_KEY=oc-test\n"
        )
        config = load_config(config_path=tmp_path / "missing.toml", env_file_path=env)
        assert config.ai.providers.openai.api_key == "sk-test"
        assert config.ai.providers.claude.api_key == "ant-test"
        assert config.ai.providers.opencode.api_key == "oc-test"

    def test_claude_key_preferred_over_anthropic(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("CLAUDE_API_KEY=claude-first\nANTHROPIC_API_KEY=anthropic-second\n")
        config = load_config(config_path=tmp_path / "missing.toml", env_file_path=env)
        assert config.ai.providers.claude.api_key == "claude-first"


class TestRedactSecrets:
    def test_api_token_redacted(self):
        config = LazyTrackConfig()
        config.jira.api_token = "secret_token"
        data = redact_secrets(config)
        assert data["jira"]["api_token"] == "[REDACTED]"

    def test_empty_token_not_shown_as_configured(self):
        config = LazyTrackConfig()
        data = redact_secrets(config)
        assert data["jira"]["api_token"] is None

    def test_providers_api_keys_redacted(self):
        config = LazyTrackConfig()
        config.ai.providers.gemini.api_key = "gemini_key"
        config.ai.providers.deepseek.api_key = "deepseek_key"
        config.ai.providers.minimax.api_key = "minimax_key"
        config.ai.providers.openai.api_key = "openai_key"
        config.ai.providers.claude.api_key = "claude_key"
        config.ai.providers.opencode.api_key = "opencode_key"
        
        data = redact_secrets(config)
        assert data["ai"]["providers"]["gemini"]["api_key"] == "[REDACTED]"
        assert data["ai"]["providers"]["deepseek"]["api_key"] == "[REDACTED]"
        assert data["ai"]["providers"]["minimax"]["api_key"] == "[REDACTED]"
        assert data["ai"]["providers"]["openai"]["api_key"] == "[REDACTED]"
        assert data["ai"]["providers"]["claude"]["api_key"] == "[REDACTED]"
        assert data["ai"]["providers"]["opencode"]["api_key"] == "[REDACTED]"
