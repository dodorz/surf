import sys
import types

import surf


def _write_config(tmp_path, lines):
    config_path = tmp_path / "config.ini"
    config_path.write_text("\n".join(lines), encoding="utf-8")
    return surf.Config(str(config_path))


def test_api_style_defaults_to_chat(tmp_path):
    config = _write_config(
        tmp_path,
        [
            "[LLM]",
            "provider = L1",
            "",
            "[LLM.L1]",
            "base_url = https://example.com/v1",
            "api_key = key",
            "model = model",
        ],
    )

    assert config.get_llm_config()["api_style"] == surf.LLM_API_STYLE_CHAT


def test_api_style_aliases_are_normalized(tmp_path):
    config = _write_config(
        tmp_path,
        [
            "[LLM]",
            "provider = A",
            "",
            "[LLM.A]",
            "api_style = OpenAI Responses",
            "base_url = https://example.com/v1",
            "api_key = key",
            "model = model",
            "",
            "[LLM.B]",
            "api_style = CLAUDE",
            "base_url = https://api.anthropic.com",
            "api_key = key",
            "model = model",
        ],
    )

    assert config.get_llm_config("A")["api_style"] == surf.LLM_API_STYLE_RESPONSES
    assert config.get_llm_config("b")["api_style"] == surf.LLM_API_STYLE_ANTHROPIC


def test_unknown_api_style_falls_back_to_chat(tmp_path):
    config = _write_config(
        tmp_path,
        [
            "[LLM]",
            "provider = L1",
            "",
            "[LLM.L1]",
            "api_style = telepathy",
            "base_url = https://example.com/v1",
            "api_key = key",
            "model = model",
        ],
    )

    assert config.get_llm_config()["api_style"] == surf.LLM_API_STYLE_CHAT


def test_chat_style_uses_chat_completions(monkeypatch):
    calls = []

    class _Completions:
        def create(self, **kwargs):
            calls.append(("chat", kwargs))
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok-chat"))]
            )

    class _Responses:
        def create(self, **kwargs):
            calls.append(("responses", kwargs))
            return types.SimpleNamespace(output_text="ok-responses")

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=_Completions())
            self.responses = _Responses()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))

    client = surf.LlmCompletionClient(
        {
            "base_url": "https://example.com/v1",
            "api_key": "key",
            "model": "model",
            "api_style": "chat",
        }
    )

    assert client.complete("system", "user") == "ok-chat"
    assert calls[0][0] == "chat"
    assert calls[0][1]["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user"},
    ]


def test_responses_style_uses_responses_api(monkeypatch):
    calls = []

    class _Completions:
        def create(self, **kwargs):
            raise AssertionError("responses style must not hit chat completions")

    class _Responses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(output_text="ok-responses")

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=_Completions())
            self.responses = _Responses()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))

    client = surf.LlmCompletionClient(
        {
            "base_url": "https://example.com/v1",
            "api_key": "key",
            "model": "model",
            "api_style": "responses",
            "max_tokens": "256",
        }
    )

    assert client.complete("system", "user") == "ok-responses"
    assert calls[0]["instructions"] == "system"
    assert calls[0]["input"] == "user"
    assert calls[0]["max_output_tokens"] == 256


def test_responses_style_extracts_output_blocks_when_output_text_missing():
    response = types.SimpleNamespace(
        output_text=None,
        output=[
            types.SimpleNamespace(
                type="message",
                content=[types.SimpleNamespace(type="output_text", text="hello ")],
            ),
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "world"}],
            },
        ],
    )

    assert surf.LlmCompletionClient._extract_responses_text(response) == "hello world"


def test_anthropic_style_posts_messages_payload(monkeypatch):
    calls = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "content": [
                    {"type": "text", "text": "你好，"},
                    {"type": "tool_use", "id": "x"},
                    {"type": "text", "text": "世界"},
                ]
            }

    def _fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return _Response()

    monkeypatch.setattr(surf, "_requests_post_interruptibly", _fake_post)

    client = surf.LlmCompletionClient(
        {
            "base_url": "https://api.anthropic.com",
            "api_key": "key",
            "model": "claude-3-5-sonnet-latest",
            "api_style": "anthropic",
        }
    )

    assert client.complete("system prompt", "user prompt") == "你好，世界"
    url, kwargs = calls[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert kwargs["headers"]["x-api-key"] == "key"
    assert kwargs["headers"]["anthropic-version"] == surf.LlmCompletionClient.DEFAULT_ANTHROPIC_VERSION
    assert kwargs["json"]["system"] == "system prompt"
    assert kwargs["json"]["messages"] == [{"role": "user", "content": "user prompt"}]
    assert kwargs["json"]["max_tokens"] == surf.LlmCompletionClient.DEFAULT_ANTHROPIC_MAX_TOKENS


def test_anthropic_base_url_variants():
    base = {"api_key": "key", "model": "m", "api_style": "anthropic"}

    assert (
        surf.LlmCompletionClient({**base, "base_url": "https://api.anthropic.com"})._anthropic_messages_url()
        == "https://api.anthropic.com/v1/messages"
    )
    assert (
        surf.LlmCompletionClient({**base, "base_url": "https://gw.example.com/v1"})._anthropic_messages_url()
        == "https://gw.example.com/v1/messages"
    )
    assert (
        surf.LlmCompletionClient({**base, "base_url": "https://gw.example.com/v1/messages/"})._anthropic_messages_url()
        == "https://gw.example.com/v1/messages"
    )


def test_anthropic_auth_header_bearer(monkeypatch):
    calls = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"content": [{"type": "text", "text": "ok"}]}

    monkeypatch.setattr(surf, "_requests_post_interruptibly", lambda url, **kwargs: calls.append(kwargs) or _Response())

    client = surf.LlmCompletionClient(
        {
            "base_url": "https://gw.example.com/v1",
            "api_key": "key",
            "model": "m",
            "api_style": "anthropic",
            "auth_header": "bearer",
            "max_tokens": "512",
            "timeout": "30",
        }
    )

    assert client.complete("s", "u") == "ok"
    headers = calls[0]["headers"]
    assert headers["Authorization"] == "Bearer key"
    assert "x-api-key" not in headers
    assert calls[0]["json"]["max_tokens"] == 512
    assert calls[0]["timeout"] == 30


def test_translate_if_needed_uses_configured_api_style(monkeypatch):
    monkeypatch.setattr(surf, "detect", lambda text: "en")
    calls = []

    class _Responses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(output_text="译文")

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=None)
            self.responses = _Responses()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))

    translated_text, translated_title = surf.ContentProcessor.translate_if_needed(
        "This is an English paragraph.",
        target_lang="zh-cn",
        config=types.SimpleNamespace(
            get_llm_config=lambda llm_provider=None: {
                "base_url": "https://example.com/v1",
                "api_key": "test-key",
                "model": "test-model",
                "api_style": "responses",
            }
        ),
    )

    assert translated_title is None
    assert translated_text == "译文"
    assert calls[0]["instructions"].startswith("You are a professional Markdown translator.")
    assert calls[0]["input"] == "This is an English paragraph."
