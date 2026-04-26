from surf import Fetcher, OutputHandler


def test_render_fx_text_html_handles_twitter_entity_dict():
    html = Fetcher._render_fx_text_html(
        "Read this",
        {"urls": [{"indices": [5, 9], "expanded_url": "https://example.com/post"}]},
    )

    assert html == "Read <a href='https://example.com/post'>this</a>"


def test_extract_fx_block_sequence_falls_back_to_tweet_text_entities():
    blocks = Fetcher._extract_fx_block_sequence(
        {},
        {
            "text": "Read this",
            "entities": {
                "urls": [{"indices": [5, 9], "expanded_url": "https://example.com/post"}]
            },
        },
    )

    assert blocks == ["<p>Read <a href='https://example.com/post'>this</a></p>"]


def test_markdown_filename_title_is_capped_for_windows_path_segments():
    title = "这是一段很长的推文标题" * 20

    safe_title = OutputHandler._safe_filename_title(title, max_len=120)

    assert len(safe_title) == 120
