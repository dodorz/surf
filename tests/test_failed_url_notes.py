"""Failed URLs are recorded in the notes directory with a 获取失败_ prefix."""

import argparse

import pytest

import surf


class _FakeConfig:
    def __init__(self, md_dir):
        self.md_dir = str(md_dir)

    def get(self, section, key, fallback=None):
        return fallback

    def get_path(self, section, key, fallback=None):
        return self.md_dir


def test_save_failed_url_note_writes_url_with_prefix(tmp_path):
    config = _FakeConfig(tmp_path)

    path = surf._save_failed_url_note(
        "https://example.com/a",
        config,
        title="Example Title",
    )

    assert path == str(tmp_path / "获取失败_Example Title.md")
    assert (tmp_path / "获取失败_Example Title.md").read_text(encoding="utf-8") == "https://example.com/a\n"


def test_save_failed_url_note_uses_url_slug_without_title(tmp_path):
    config = _FakeConfig(tmp_path)
    url = "https://www.example.com/a/b?x=1"

    path = surf._save_failed_url_note(url, config)

    assert path is not None
    assert path.startswith(str(tmp_path / "获取失败_example.com_a_b"))
    assert (tmp_path / (path.split("/")[-1])).read_text(encoding="utf-8") == url + "\n"


def test_save_failed_url_note_does_not_clobber_same_title(tmp_path):
    config = _FakeConfig(tmp_path)

    first = surf._save_failed_url_note("https://one.example/a", config, title="Same Title")
    second = surf._save_failed_url_note("https://two.example/b", config, title="Same Title")

    assert first != second
    assert second.endswith("获取失败_Same Title_2.md")


def test_save_failed_url_note_reuses_file_for_same_url(tmp_path):
    config = _FakeConfig(tmp_path)

    first = surf._save_failed_url_note("https://example.com/a", config, title="Same Title")
    second = surf._save_failed_url_note("https://example.com/a", config, title="Same Title")

    assert first == second


def test_save_failed_url_note_skips_non_http_inputs(tmp_path):
    config = _FakeConfig(tmp_path)

    assert surf._save_failed_url_note("/tmp/local.html", config) is None
    assert surf._save_failed_url_note("just some keywords", config) is None


def _search_args(**overrides):
    defaults = dict(
        url="demo keywords",
        output=None,
        search_engine=None,
        search_limit=None,
        search_preview=None,
        search_top=2,
        feel_lucky=False,
        search_merge=False,
        search_prefix=None,
        search_delay=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_search_batch_writes_failed_url_notes(tmp_path, monkeypatch):
    results = [
        {"title": "First result", "url": "https://one.example/a"},
        {"title": "Second result", "url": "https://two.example/b"},
    ]
    monkeypatch.setattr(surf.SearchHandler, "search", classmethod(lambda cls, *a, **k: results))
    monkeypatch.setattr(surf, "_prepare_single_url", lambda *a, **k: (_ for _ in ()).throw(SystemExit(1)))

    with pytest.raises(SystemExit):
        surf._run_search_batch(_search_args(), _FakeConfig(tmp_path), argparse.ArgumentParser(), None, None)

    notes = {path.name: path.read_text(encoding="utf-8") for path in tmp_path.glob("*.md")}
    assert notes == {
        "获取失败_First result.md": "https://one.example/a\n",
        "获取失败_Second result.md": "https://two.example/b\n",
    }


def test_single_url_failure_writes_failed_url_note(tmp_path, monkeypatch):
    monkeypatch.setattr(surf, "_prepare_single_url", lambda *a, **k: (_ for _ in ()).throw(SystemExit(1)))
    args = argparse.Namespace(url="https://example.com/fail")

    with pytest.raises(SystemExit):
        surf._process_single_url(args, _FakeConfig(tmp_path), argparse.ArgumentParser(), None, None)

    saved = tmp_path / "获取失败_example.com_fail.md"
    assert saved.read_text(encoding="utf-8") == "https://example.com/fail\n"
