import os

import pytest

from surf import OutputHandler


class _FakeConfig:
    def get_path(self, section, key, fallback=None):
        return fallback


def test_save_markdown_refuses_empty_content(tmp_path):
    target = tmp_path / "Chat.md"
    with pytest.raises(ValueError):
        OutputHandler.save_markdown("Chat", "", _FakeConfig(), output_path=str(target))
    assert not target.exists()


def test_save_markdown_refuses_whitespace_only_content(tmp_path):
    target = tmp_path / "Chat.md"
    with pytest.raises(ValueError):
        OutputHandler.save_markdown("Chat", "   \n\t ", _FakeConfig(), output_path=str(target))
    assert not target.exists()


def test_save_markdown_allows_real_content(tmp_path):
    target = tmp_path / "Chat.md"
    saved = OutputHandler.save_markdown("Chat", "# Hello\n", _FakeConfig(), output_path=str(target))
    assert os.path.isfile(saved)
    assert target.read_text(encoding="utf-8") == "# Hello\n"


def test_save_markdown_allows_front_matter_only(tmp_path):
    html = "<html><head><title>Doc</title></head><body></body></html>"
    target = tmp_path / "Doc.md"
    saved = OutputHandler.save_markdown(
        "Doc",
        "",
        _FakeConfig(),
        output_path=str(target),
        html_content=html,
    )
    assert os.path.isfile(saved)
    assert os.path.getsize(saved) > 0
