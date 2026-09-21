import json

import research_runtime
import ultimate_bot


def test_remote_ollama_skips_localhost(monkeypatch):
    monkeypatch.setenv("VSF_REMOTE_MODE", "1")
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.setattr(
        research_runtime,
        "_script_evidence_text",
        lambda _story: "Supported evidence sentence one.",
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("localhost Ollama must not be called in remote mode")

    monkeypatch.setattr(research_runtime, "_call_chat_completion", fail_if_called)

    result = research_runtime._ollama_script_fallback({}, {}, "national_global_affairs", "regular")
    assert result is None


def test_remote_ollama_allows_explicit_non_local_endpoint(monkeypatch):
    monkeypatch.setenv("VSF_REMOTE_MODE", "1")
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://remote-ollama.example")
    monkeypatch.setattr(
        research_runtime,
        "_script_evidence_text",
        lambda _story: "Supported evidence sentence one.",
    )
    monkeypatch.setattr(
        research_runtime,
        "_call_chat_completion",
        lambda *args, **kwargs: {"provider": "remote-ollama"},
    )

    result = research_runtime._ollama_script_fallback({}, {}, "national_global_affairs", "regular")
    assert result == {"provider": "remote-ollama"}


def test_remote_youtube_credentials_use_secret_without_local_files(monkeypatch):
    payload = {
        "token": "access-token",
        "refresh_token": "refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "scopes": ultimate_bot.OAUTH_SCOPES,
    }
    monkeypatch.setenv("VSF_REMOTE_MODE", "1")
    monkeypatch.setenv("YOUTUBE_TOKEN_JSON", json.dumps(payload))
    monkeypatch.setattr(ultimate_bot.os.path, "exists", lambda _path: False)

    class FakeCredentials:
        valid = True
        expired = False
        refresh_token = "refresh-token"

    from google.oauth2 import credentials as google_credentials
    monkeypatch.setattr(
        google_credentials.Credentials,
        "from_authorized_user_info",
        staticmethod(lambda info, scopes: FakeCredentials()),
    )

    creds = ultimate_bot.get_google_credentials()
    assert isinstance(creds, FakeCredentials)


def test_remote_secret_bridge_names_cover_visual_provider_keys():
    import app

    assert "SERPAPI_API_KEY" in app.REQUIRED_SECRET_NAMES
    assert "PIXABAY_API_KEY" in app.REQUIRED_SECRET_NAMES
    assert "OPENALEX_API_KEY" in app.REQUIRED_SECRET_NAMES


def test_global_exception_hook_does_not_block_noninteractive_runtime(monkeypatch):
    class _NonInteractiveStdin:
        @staticmethod
        def isatty():
            return False

    monkeypatch.setattr(ultimate_bot.sys, "stdin", _NonInteractiveStdin())

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("non-interactive exception handling must not wait for console input")

    monkeypatch.setattr("builtins.input", fail_if_called)
    ultimate_bot.global_exception_hook(ValueError, ValueError("diagnostic"), None)


def test_global_exception_hook_preserves_local_interactive_pause(monkeypatch):
    class _InteractiveStdin:
        @staticmethod
        def isatty():
            return True

    calls = []
    monkeypatch.setattr(ultimate_bot.sys, "stdin", _InteractiveStdin())
    monkeypatch.delenv("VSF_REMOTE_MODE", raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt: calls.append(prompt))
    ultimate_bot.global_exception_hook(ValueError, ValueError("diagnostic"), None)
    assert calls == ["\nPress Enter to exit..."]
