"""Translation must not silently return an untranslated document.

A long article is split into many chunks; a single flaky provider response used
to abort the whole translation (the caller then produced the source text), which
looked like "translation randomly does nothing".
"""
import surf
from surf import ContentProcessor


class _FakeConfig:
    def get_llm_config(self, provider=None):
        return {"base_url": "http://example.invalid", "api_key": "k", "model": "m"}

    def get(self, section, key, fallback=None):
        return fallback


def _install_client(monkeypatch, behaviour):
    calls = []

    class _Client:
        def __init__(self, cfg):
            pass

        def complete(self, system_prompt, user_prompt):
            calls.append(user_prompt)
            return behaviour(len(calls), user_prompt)

    monkeypatch.setattr(surf, "LlmCompletionClient", _Client)
    monkeypatch.setattr(ContentProcessor, "TRANSLATION_RETRY_DELAY", 0)
    return calls


def test_a_flaky_chunk_is_retried_instead_of_aborting(monkeypatch):
    calls = _install_client(
        monkeypatch, lambda n, _p: (_ for _ in ()).throw(RuntimeError("boom")) if n == 1 else "译文"
    )
    translated, _title = ContentProcessor.translate_if_needed(
        "An English paragraph that has to be translated.", target_lang="zh-cn", config=_FakeConfig()
    )
    assert len(calls) == 2
    assert translated == "译文"


def test_a_persistently_failing_chunk_keeps_its_source_text(monkeypatch):
    calls = _install_client(monkeypatch, lambda _n, _p: (_ for _ in ()).throw(RuntimeError("down")))
    source = "An English paragraph that has to be translated."
    translated, _title = ContentProcessor.translate_if_needed(
        source, target_lang="zh-cn", config=_FakeConfig()
    )
    assert len(calls) == ContentProcessor.TRANSLATION_ATTEMPTS
    # The failure is reported by keeping the original chunk, not by an empty doc.
    assert translated == source


def test_chunks_after_a_failure_are_still_translated(monkeypatch):
    # Each paragraph is big enough to become its own chunk (> _chunk_text limit).
    paragraphs = [
        f"Paragraph {index}. " + ("English filler text. " * 200) for index in range(3)
    ]
    source = "\n\n".join(paragraphs)
    attempts = ContentProcessor.TRANSLATION_ATTEMPTS

    def behaviour(n, _prompt):
        if n <= attempts:  # every attempt of the first chunk fails
            raise RuntimeError("provider down")
        return f"译文{n}"

    calls = _install_client(monkeypatch, behaviour)
    translated, _title = ContentProcessor.translate_if_needed(
        source, target_lang="zh-cn", config=_FakeConfig()
    )
    # The first chunk failed (its source text is kept), the others were translated.
    assert paragraphs[0] in translated
    assert "译文" in translated


def test_language_detection_is_deterministic():
    from surf import detect

    sample = "Surf takes a web page, a local file, or a search query and turns it into Markdown."
    assert {detect(sample) for _ in range(25)} == {"en"}


def test_save_job_translation_timeout_is_configurable(tmp_path):
    import surf_web
    from surf import Config

    config = Config(str(tmp_path / "missing.ini"))
    original = surf_web.get_config
    try:
        surf_web.get_config = lambda: config
        assert surf_web._translation_job_timeout_seconds() == 1800
        config.set("LLM", "translation_job_timeout", "600")
        assert surf_web._translation_job_timeout_seconds() == 600
        config.set("LLM", "translation_job_timeout", "0")
        assert surf_web._translation_job_timeout_seconds() == 60
        config.set("LLM", "translation_job_timeout", "junk")
        assert surf_web._translation_job_timeout_seconds() == 1800
    finally:
        surf_web.get_config = original
