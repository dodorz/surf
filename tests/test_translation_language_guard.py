import surf


class _NoLlmConfig:
    def get_llm_config(self, llm_provider=None):
        raise AssertionError("Already-Chinese mixed Markdown should not call the LLM")


def test_mixed_chinese_markdown_skips_translation_even_if_detected_as_english(monkeypatch):
    monkeypatch.setattr(surf, "detect", lambda text: "en")

    text = "\n".join(
        [
            "# PPT Master - AI native editable PPTX",
            "[![Version](https://img.shields.io/badge/version-v2.6.0-blue.svg)](https://example.com)",
            "[English](./README.md) | 中文",
            "本项目由赞助方支持，得以持续免费开源。",
            "这是一个中文说明文档，包含足够多的中文内容，用于避免被开头的徽章、URL 和英文项目名误判。",
            "它支持任意文档输入、自动生成大纲、创建原生可编辑演示文稿，并保留清晰的 Markdown 结构。",
            "更多中文段落可以帮助语言判断确认目标语言已经存在，不需要再调用翻译模型。",
            "使用者可以通过配置文件设置模型、代理、输出目录和其他参数。",
        ]
    )

    translated_text, translated_title = surf.ContentProcessor.translate_if_needed(
        text,
        title="README_CN.md",
        target_lang="zh-cn",
        config=_NoLlmConfig(),
    )

    assert translated_text == text
    assert translated_title == "README_CN.md"


def test_sparse_chinese_markdown_still_allows_translation_path(monkeypatch):
    monkeypatch.setattr(surf, "detect", lambda text: "en")

    calls = {"called": False}

    class _FakeConfig:
        def get_llm_config(self, llm_provider=None):
            calls["called"] = True
            raise ValueError("stop before network")

    text = "# English Guide\n\nThis document mentions 中文 once but is mostly English."

    translated_text, translated_title = surf.ContentProcessor.translate_if_needed(
        text,
        title="Guide",
        target_lang="zh-cn",
        config=_FakeConfig(),
    )

    assert calls["called"] is True
    assert translated_text == text
    assert translated_title == "Guide"
