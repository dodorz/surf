from array import array
import sys
from types import SimpleNamespace

import surf
from surf import Fetcher, OutputHandler, _extract_direct_markdown_payload


PODCAST_ID = "afc4d300-505a-013c-f72d-0acc26574db2"
EPISODE_ID = "ef99d8d4-a16c-48c3-a844-9a88163450d3"
CANONICAL_URL = (
    "https://pocketcasts.com/podcast/my-show/"
    f"{PODCAST_ID}/my-episode/{EPISODE_ID}"
)


def test_pocketcasts_identifiers_support_zero_version_uuid():
    info = Fetcher._extract_pocketcasts_episode_identifiers(CANONICAL_URL)

    assert info["podcast_id"] == PODCAST_ID
    assert info["episode_id"] == EPISODE_ID
    assert info["podcast_title"] == "my-show"
    assert info["episode_title"] == "my-episode"


def test_pocketcasts_page_info_prefers_jsonld_metadata():
    html = """
    <html><head>
      <meta property="og:title" content="Open Graph title">
      <meta property="og:description" content="Open Graph notes">
      <script type="application/ld+json">
      {
        "@type": "PodcastEpisode",
        "name": "JSON-LD episode",
        "description": "Episode notes",
        "datePublished": "2025-01-02T03:04:05Z",
        "duration": "PT42M",
        "author": {"name": "Host"},
        "partOfSeries": {"name": "The Show"},
        "associatedMedia": {"contentUrl": "https://audio.example/episode.mp3"}
      }
      </script>
    </head></html>
    """

    info = Fetcher._pocketcasts_page_info(html, CANONICAL_URL)

    assert info["episode_title"] == "JSON-LD episode"
    assert info["description"] == "Episode notes"
    assert info["podcast_title"] == "The Show"
    assert info["author"] == "Host"
    assert info["published"] == "2025-01-02T03:04:05Z"
    assert info["duration"] == "PT42M"
    assert info["audio_url"] == "https://audio.example/episode.mp3"


def test_pocketcasts_rss_matches_episode_uuid(monkeypatch):
    rss = f"""<?xml version="1.0"?>
    <rss xmlns:content="http://purl.org/rss/1.0/modules/content/">
      <channel><title>The Show</title>
        <item>
          <guid>{EPISODE_ID}</guid>
          <title>Episode from RSS</title>
          <pubDate>Thu, 02 Jan 2025 03:04:05 GMT</pubDate>
          <itunes:duration>00:42:00</itunes:duration>
          <content:encoded><![CDATA[<p><strong>Show notes</strong></p>]]></content:encoded>
          <enclosure url="https://audio.example/episode.mp3" />
        </item>
      </channel>
    </rss>"""

    class DummyResponse:
        content = rss.encode("utf-8")
        headers = {"Content-Type": "application/rss+xml; charset=utf-8"}

        def raise_for_status(self):
            return None

    monkeypatch.setattr("surf._requests_get_interruptibly", lambda *args, **kwargs: DummyResponse())
    monkeypatch.setattr(
        "surf.Fetcher._get_proxies",
        lambda config, proxy_mode_override=None, custom_proxy_override=None: (None, None),
    )

    info = Fetcher._fetch_pocketcasts_rss(
        "https://feeds.example/show.xml",
        EPISODE_ID,
        "Unknown title",
        {},
        None,
        None,
    )

    assert info["episode_title"] == "Episode from RSS"
    assert "Show notes" in info["description"]
    assert info["duration"] == "00:42:00"
    assert info["audio_url"] == "https://audio.example/episode.mp3"




def test_transcribe_cpp_appends_timestamped_transcript(monkeypatch, tmp_path):
    class Segment:
        t0_ms = 1250
        text = "Hello from the podcast"

    class FakeResult:
        segments = (Segment(),)
        text = "Hello from the podcast"

    class FakeTranscribeCpp:
        @staticmethod
        def transcribe(*args, **kwargs):
            return FakeResult()

    class AudioResponse:
        headers = {}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=0):
            yield b"audio"

        def close(self):
            return None

    def fake_ffmpeg(command, **kwargs):
        with open(command[-1], "wb") as pcm_file:
            pcm_file.write(array("f", [0.0] * 16000).tobytes())
        return SimpleNamespace(stdout="", stderr="")

    class Config:
        def get(self, section, key, fallback=""):
            return {
                "model_path": str(tmp_path / "model.gguf"),
                "backend": "cpu",
                "language": "auto",
                "ffmpeg_path": "ffmpeg",
                "max_audio_mb": "1",
            }.get(key, fallback)

    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"fake model")
    html = surf._build_direct_markdown_payload(
        "**Audio:** [Play episode](https://audio.example/episode.mp3)",
        "Episode - Show",
        CANONICAL_URL,
        "pocketcasts",
        extra_meta={"surf-audio-url": "https://audio.example/episode.mp3"},
    )

    monkeypatch.setitem(sys.modules, "transcribe_cpp", FakeTranscribeCpp)
    monkeypatch.setattr("surf._requests_get_interruptibly", lambda *args, **kwargs: AudioResponse())
    monkeypatch.setattr("surf._run_subprocess_interruptibly", fake_ffmpeg)
    monkeypatch.setattr(
        "surf.Fetcher._get_proxies",
        lambda config, proxy_mode_override=None, custom_proxy_override=None: (None, None),
    )

    result = Fetcher._transcribe_podcast_content(html, Config())
    markdown = _extract_direct_markdown_payload(result)["markdown"]

    assert "## Transcript" in markdown
    assert "[00:00:01] Hello from the podcast" in markdown


def test_pocketcasts_handler_keeps_show_notes_and_audio_link(monkeypatch):
    html = """
    <html><head>
      <script type="application/ld+json">
      {"@type":"PodcastEpisode","name":"Episode","description":"<p>Show notes body</p>",
       "partOfSeries":{"name":"Show"},"associatedMedia":{"contentUrl":"https://audio.example/a.mp3"}}
      </script>
    </head></html>
    """

    class PageResponse:
        content = html.encode("utf-8")
        headers = {"Content-Type": "text/html; charset=utf-8"}
        url = CANONICAL_URL

        def raise_for_status(self):
            return None

    monkeypatch.setattr("surf._requests_get_interruptibly", lambda *args, **kwargs: PageResponse())
    monkeypatch.setattr(
        "surf.Fetcher._get_proxies",
        lambda config, proxy_mode_override=None, custom_proxy_override=None: (None, None),
    )
    monkeypatch.setattr("surf.Fetcher.fetch_with_browser", lambda *args, **kwargs: None)

    result = Fetcher._fetch_pocketcasts_episode(CANONICAL_URL, {}, None, None)
    markdown = _extract_direct_markdown_payload(result)["markdown"]

    assert "## Show Notes" in markdown
    assert "Show notes body" in markdown
    assert "[Play episode](https://audio.example/a.mp3)" in markdown
    assert OutputHandler._extract_metadata(result, source_url=CANONICAL_URL)["description"] is None


def test_pocketcasts_handler_returns_redirect_metadata_when_page_is_blocked(monkeypatch):
    short_url = f"https://pca.st/episode/{EPISODE_ID}"

    monkeypatch.setattr("surf.Fetcher._resolve_url_with_redirects", lambda *args, **kwargs: CANONICAL_URL)

    class BlockedResponse:
        content = b""
        headers = {"Content-Type": "text/html"}
        url = CANONICAL_URL

        def raise_for_status(self):
            return None

    monkeypatch.setattr("surf._requests_get_interruptibly", lambda *args, **kwargs: BlockedResponse())
    monkeypatch.setattr(
        "surf.Fetcher._get_proxies",
        lambda config, proxy_mode_override=None, custom_proxy_override=None: (None, None),
    )
    monkeypatch.setattr("surf.Fetcher.fetch_with_browser", lambda *args, **kwargs: None)

    result = Fetcher._fetch_pocketcasts_episode(short_url, {}, None, None)
    payload = _extract_direct_markdown_payload(result)

    assert payload["title"] == "my-episode - my-show"
    assert payload["site_name"] == "pocketcasts"
    assert EPISODE_ID in payload["markdown"] or EPISODE_ID in result
    metadata = OutputHandler._extract_metadata(result, source_url=CANONICAL_URL)
    assert metadata["title"] == "my-episode - my-show"
    assert metadata["description"] is None
    assert metadata["author"] == "my-show"
    assert OutputHandler._get_filename_title(
        metadata["title"], html_content=result
    ) == "[播客] my-episode - my-show"
