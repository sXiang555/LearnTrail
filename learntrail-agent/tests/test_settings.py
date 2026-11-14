"""Configuration tests use temporary dummy values only."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from learntrail_agent.settings import PROJECT_ROOT, Settings


def test_dotenv_is_project_relative_and_environment_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings.model_config["env_file"] == PROJECT_ROOT / ".env"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LLM_API_KEY=test-only-file-key\nLLM_BASE_URL=https://model.example.test/v1/\n"
        "LLM_MODEL=file-model\nLLM_TEMPERATURE=\nDATABASE_URL=\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_MODEL", "environment-model")
    settings = Settings(_env_file=env_file)
    assert settings.llm_model == "environment-model"
    assert settings.llm_base_url == "https://model.example.test/v1"
    assert settings.llm_api_key.get_secret_value() == "test-only-file-key"
    assert settings.llm_temperature is None
    assert settings.llm_configured
    assert "test-only-file-key" not in repr(settings)
    assert "test-only-file-key" not in settings.model_dump_json()


def test_missing_model_settings_are_allowed_for_health() -> None:
    settings = Settings(_env_file=None, llm_api_key="", llm_model="", llm_base_url="")
    assert not settings.llm_configured


@pytest.mark.parametrize("url", [
    "file:///tmp/model", "https://user:test-only-password@example.test/v1",
    "https://example.test/v1?key=test-only-key", "https://example.test/v1#fragment",
    "https://example.test:99999/v1", "https://example.test/bad path",
])
def test_unsafe_base_url_is_rejected_without_echoing_input(url: str) -> None:
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, llm_base_url=url)
    assert url not in str(caught.value)


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_invalid_timeout_is_rejected(value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_timeout_seconds=value)


def test_api_key_header_injection_is_rejected() -> None:
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, llm_api_key="test-only-key\r\nInjected: value")
    assert "test-only-key" not in str(caught.value)
