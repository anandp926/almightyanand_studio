from agents import settings


def test_dotenv_strips_inline_comments_and_quotes(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        'GOOGLE_API_KEY=                          # Gemini free tier\n'
        'GROQ_API_KEY=gsk_abc   # groq\n'
        'QUOTED="hello world"\n'
        '# full comment line\n'
    )
    monkeypatch.setattr(settings, "ENV_FILE", env_file)
    settings.reload()
    assert settings.env("GOOGLE_API_KEY") is None          # a comment is not a key
    assert settings.env("GROQ_API_KEY") == "gsk_abc"
    assert settings.env("QUOTED") == "hello world"


def test_empty_environment_value_falls_back_to_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("GROQ_API_KEY=from_file\n")
    monkeypatch.setattr(settings, "ENV_FILE", env_file)
    settings.reload()
    monkeypatch.setenv("GROQ_API_KEY", "")
    assert settings.env("GROQ_API_KEY") == "from_file"
    monkeypatch.setenv("GROQ_API_KEY", "from_env")
    assert settings.env("GROQ_API_KEY") == "from_env"
