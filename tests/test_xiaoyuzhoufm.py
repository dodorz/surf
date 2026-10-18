import configparser
import json

import surf
from surf import Fetcher, OutputHandler, _extract_direct_markdown_payload


EPISODE_ID = "69018660740634ca4785c3b8"
PODCAST_ID = "62382c1103bea1ebfffa1c00"
EPISODE_URL = f"https://www.xiaoyuzhoufm.com/episode/{EPISODE_ID}"

NEXT_DATA_EPISODE = {
    "type": "EPISODE",
    "eid": EPISODE_ID,
    "pid": PODCAST_ID,
    "title": "第 12 期 播客示例",
    "shownotes": "<p><strong>Shownotes</strong> 正文</p>",
    "duration": 8831,
    "pubDate": "2025-10-29T12:00:00.000Z",
    "enclosure": {"url": "https://media.xyzcdn.net/example/ep12.m4a"},
    "podcast": {
        "type": "PODCAST",
        "pid": PODCAST_ID,
        "title": "示例播客",
        "author": "主播甲",
    },
}


def xiaoyuzhou_html(episode=NEXT_DATA_EPISODE):
    script = json.dumps({"props": {"pageProps": {"episode": episode}}}, ensure_ascii=False)
    return (
        "<html><head>"
        '<script id="__NEXT_DATA__" type="application/json">'
        f"{script}"
        "</script></head><body></body></html>"
    )


def test_xiaoyuzhoufm_share_url_routes_to_handler():
    handler, site_name, site_config = surf._get_handler_for_url(EPISODE_URL)

    assert handler is Fetcher._fetch_xiaoyuzhoufm_episode
    assert site_name == "xiaoyuzhoufm"
    assert site_config.get("no_generic_fallback") is True


def test_xiaoyuzhoufm_page_info_parses_next_data():
    info = Fetcher._xiaoyuzhoufm_page_info(xiaoyuzhou_html(), EPISODE_URL)

    assert info["episode_id"] == EPISODE_ID
    assert info["podcast_id"] == PODCAST_ID
    assert info["episode_title"] == "第 12 期 播客示例"
    assert info["podcast_title"] == "示例播客"
    assert info["author"] == "主播甲"
    assert info["description"] == NEXT_DATA_EPISODE["shownotes"]
    assert info["published"] == "2025-10-29T12:00:00.000Z"
    assert info["duration"] == "02:27:11"
    assert info["audio_url"] == "https://media.xyzcdn.net/example/ep12.m4a"


def test_xiaoyuzhoufm_duration_keeps_non_numeric_values():
    assert Fetcher._xiaoyuzhoufm_format_duration("PT42M") == "PT42M"
    assert Fetcher._xiaoyuzhoufm_format_duration(None) == ""
    assert Fetcher._xiaoyuzhoufm_format_duration(0) == ""


def test_xiaoyuzhoufm_audio_downloads_original_enclosure(monkeypatch, tmp_path):
    class AudioResponse:
        headers = {"Content-Type": "audio/x-m4a", "Content-Length": "10"}
        url = "https://media.xyzcdn.net/example/episode.m4a"

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=0):
            yield b"audio-data"

        def close(self):
            return None

    monkeypatch.setattr("surf._requests_get_interruptibly", lambda *args, **kwargs: AudioResponse())
    monkeypatch.setattr(
        "surf.Fetcher._get_proxies",
        lambda config, proxy_mode_override=None, custom_proxy_override=None: (None, None),
    )

    output_path = tmp_path / "episode.m4a"
    result = Fetcher._download_podcast_audio(
        _build_payload_for_download(),
        configparser.ConfigParser(),
        output_path=str(output_path),
    )

    assert result == str(output_path)
    assert output_path.read_bytes() == b"audio-data"
    assert not (tmp_path / "episode.m4a.part").exists()


def _build_payload_for_download():
    return surf._build_direct_markdown_payload(
        "**Audio:** [Play episode](https://media.xyzcdn.net/example/episode.m4a)",
        "第 12 期播客",
        EPISODE_URL,
        "xiaoyuzhoufm",
        extra_meta={"surf-audio-url": "https://media.xyzcdn.net/example/episode.m4a"},
    )



def test_xiaoyuzhoufm_paid_episode_uses_public_preview(monkeypatch, tmp_path):
    episode = dict(NEXT_DATA_EPISODE)
    episode.update(
        {
            "isPrivateMedia": True,
            "isOwned": False,
            "media": {"size": 74004504, "mimeType": "audio/mp4"},
            "trial": {
                "segment": "https://media.xyzcdn.net/example/preview.m4a",
                "to": 180,
            },
        }
    )
    html = xiaoyuzhou_html(episode)
    info = Fetcher._xiaoyuzhoufm_page_info(html, EPISODE_URL)

    assert info["audio_trial_only"] is True
    assert info["audio_trial_url"] == "https://media.xyzcdn.net/example/preview.m4a"
    assert info["audio_trial_duration"] == "00:03:00"

    class AudioResponse:
        headers = {"Content-Type": "audio/x-m4a", "Content-Length": "10"}
        url = "https://media.xyzcdn.net/example/preview.m4a"

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=0):
            yield b"preview!!!"

        def close(self):
            return None

    requested = []
    monkeypatch.setattr(
        "surf._requests_get_interruptibly",
        lambda url, **kwargs: (requested.append(url) or AudioResponse()),
    )
    monkeypatch.setattr(
        "surf.Fetcher._get_proxies",
        lambda config, proxy_mode_override=None, custom_proxy_override=None: (None, None),
    )
    payload = Fetcher._build_podcast_episode_payload(
        info, EPISODE_URL, EPISODE_URL, "xiaoyuzhoufm", "Xiaoyuzhou"
    )
    output_path = tmp_path / "preview.m4a"
    Fetcher._download_podcast_audio(payload, configparser.ConfigParser(), output_path=str(output_path))

    assert requested == ["https://media.xyzcdn.net/example/preview.m4a"]
    assert output_path.read_bytes() == b"preview!!!"



def test_xiaoyuzhoufm_handler_falls_back_to_og_meta(monkeypatch):
    html = """
    <html><head>
      <meta property="og:title" content="OG Episode">
      <meta name="description" property="og:description" content="<p>OG notes</p>">
      <meta property="og:audio" content="https://media.xyzcdn.net/example/og.m4a">
    </head></html>
    """

    class PageResponse:
        content = html.encode("utf-8")
        headers = {"Content-Type": "text/html; charset=utf-8"}
        url = EPISODE_URL

        def raise_for_status(self):
            return None

    monkeypatch.setattr("surf._requests_get_interruptibly", lambda *args, **kwargs: PageResponse())
    monkeypatch.setattr(
        "surf.Fetcher._get_proxies",
        lambda config, proxy_mode_override=None, custom_proxy_override=None: (None, None),
    )
    monkeypatch.setattr("surf.Fetcher.fetch_with_browser", lambda *args, **kwargs: None)

    result = Fetcher._fetch_xiaoyuzhoufm_episode(EPISODE_URL, {}, None, None)
    payload = _extract_direct_markdown_payload(result)

    assert payload["site_name"] == "xiaoyuzhoufm"
    assert payload["title"] == "OG Episode"
    assert "OG notes" in payload["markdown"]
    assert "[Play episode](https://media.xyzcdn.net/example/og.m4a)" in payload["markdown"]
    assert EPISODE_ID in payload["markdown"]


def test_xiaoyuzhoufm_handler_builds_payload_with_show_notes_and_audio(monkeypatch):
    class PageResponse:
        content = xiaoyuzhou_html().encode("utf-8")
        headers = {"Content-Type": "text/html; charset=utf-8"}
        url = EPISODE_URL

        def raise_for_status(self):
            return None

    monkeypatch.setattr("surf._requests_get_interruptibly", lambda *args, **kwargs: PageResponse())
    monkeypatch.setattr(
        "surf.Fetcher._get_proxies",
        lambda config, proxy_mode_override=None, custom_proxy_override=None: (None, None),
    )
    monkeypatch.setattr("surf.Fetcher.fetch_with_browser", lambda *args, **kwargs: None)

    result = Fetcher._fetch_xiaoyuzhoufm_episode(EPISODE_URL, configparser.ConfigParser(), None, None)
    payload = _extract_direct_markdown_payload(result)

    assert payload["title"] == "第 12 期 播客示例 - 示例播客"
    assert "**Podcast:** 示例播客" in payload["markdown"]
    assert "**Episode ID:** " + EPISODE_ID in payload["markdown"]
    assert "**Duration:** 02:27:11" in payload["markdown"]
    assert "[Play episode](https://media.xyzcdn.net/example/ep12.m4a)" in payload["markdown"]
    assert "## Show Notes" in payload["markdown"]

    metadata = OutputHandler._extract_metadata(result, source_url=EPISODE_URL)
    assert metadata["author"] == "主播甲"
    assert metadata["description"] is None
    assert OutputHandler._get_filename_title(
        metadata["title"], html_content=result
    ).startswith("[播客] ")


def test_xiaoyuzhoufm_transcript_translates_independently_of_chinese_notes(monkeypatch):
    calls = []

    def fake_translate(
        text,
        title=None,
        target_lang="zh-cn",
        config=None,
        llm_provider=None,
        protected_markdown_line_pattern=None,
        extra_system_instruction=None,
    ):
        calls.append({"text": text, "extra": extra_system_instruction})
        if text.startswith("## Transcript"):
            return "## Transcript\n\n[00:00:01] 来自播客的问候\n", title
        return text, title

    monkeypatch.setattr(surf.ContentProcessor, "translate_if_needed", fake_translate)

    markdown = (
        "**Podcast:** 示例播客\n\n"
        "## Show Notes\n\n"
        "这是一期关于气候的播客说明。\n\n"
        "## Transcript\n\n"
        "[00:00:01] Hello from the podcast\n"
    )

    translated, _ = surf._translate_markdown_document(
        markdown,
        title="第 12 期 播客示例 - 示例播客",
        target_lang="zh-cn",
        config=object(),
        source_site="xiaoyuzhoufm",
    )

    assert len(calls) == 2
    assert calls[1]["extra"]
    assert "[00:00:01] 来自播客的问候" in translated
    assert "[00:00:01] Hello from the podcast" not in translated
