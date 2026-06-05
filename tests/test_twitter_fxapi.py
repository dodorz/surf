from surf import AuthHandler, Fetcher, OutputHandler


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


def test_extract_twitter_cli_thread_items_respects_author_scope():
    payload = {
        "ok": True,
        "data": [
            {
                "id": "1",
                "text": "Current post",
                "author": {"id": "a1", "name": "Alice", "screenName": "alice"},
                "createdAtLocal": "today",
            },
            {
                "id": "2",
                "text": "Same-author reply",
                "author": {"id": "a1", "name": "Alice", "screenName": "alice"},
                "createdAtLocal": "today",
            },
            {
                "id": "3",
                "text": "Other reply",
                "author": {"id": "b1", "name": "Bob", "screenName": "bob"},
                "createdAtLocal": "today",
            },
        ],
    }

    same_items, same_index = Fetcher._extract_twitter_cli_thread_items(payload, "1", "after", "same")
    assert [item["text"] for item in same_items] == ["Current post", "Same-author reply"]
    assert same_index == 0

    all_items, all_index = Fetcher._extract_twitter_cli_thread_items(payload, "1", "after", "all")
    assert [item["text"] for item in all_items] == ["Current post", "Same-author reply", "Other reply"]
    assert all_index == 0


def test_import_twitter_cookie_values_builds_auth_state(monkeypatch):
    saved = {}

    def fake_save_state(site_name, state):
        saved["site_name"] = site_name
        saved["state"] = state

    monkeypatch.setattr("surf.AuthHandler.save_state", fake_save_state)

    assert AuthHandler.import_twitter_cookie_values("token", "csrf")
    assert saved["site_name"] == "twitter"
    cookie_names = {cookie["name"] for cookie in saved["state"]["cookies"]}
    assert {"auth_token", "ct0"}.issubset(cookie_names)


def test_apply_twitter_cookie_env_uses_saved_state(monkeypatch):
    monkeypatch.setattr(
        "surf.AuthHandler.load_state",
        lambda site_name, log_load=False: {
            "cookies": [
                {"name": "auth_token", "value": "token", "domain": ".x.com"},
                {"name": "ct0", "value": "csrf", "domain": ".x.com"},
                {"name": "guest_id", "value": "guest", "domain": ".x.com"},
            ]
        },
    )

    env = {}
    AuthHandler.apply_twitter_cookie_env(env)

    assert env["TWITTER_AUTH_TOKEN"] == "token"
    assert env["TWITTER_CT0"] == "csrf"
    assert "guest_id=guest" in env["TWITTER_COOKIE_STRING"]


def test_repair_twitter_cli_mojibake_text():
    mojibake = "å€ŸAIä¹‹ç®—åŠ›"

    assert Fetcher._repair_twitter_cli_mojibake_text(mojibake) == "借AI之算力"


def test_convert_twitter_cli_html_moves_author_and_source_to_metadata():
    payload = {
        "data": {
            "author": {"name": "Li Jigang", "screen_name": "lijigang"},
            "full_text": "原帖正文第一句。第二句。",
        }
    }

    html = Fetcher._convert_twitter_cli_json_to_html(payload, "https://x.com/lijigang/status/1")

    assert html is not None
    assert 'name="surf-author"' in html
    assert 'content="@lijigang"' in html
    assert "https://x.com/lijigang/status/1</a>" not in html
    assert "Author: @lijigang" not in html


def test_thread_context_does_not_replace_twitter_source_title():
    html = """
    <html>
      <head>
        <title>李继刚 - X Post</title>
        <meta name="surf-source-site" content="twitter">
        <meta name="surf-author" content="@lijigang">
      </head>
      <body>
        <article>
          <h1>李继刚</h1>
          <p>原帖正文第一句。第二句。</p>
          <section class="surf-thread-context">
            <h2>Thread Context</h2>
            <section class="surf-thread-post">
              <h2>Later Reply</h2>
              <p><strong>Other</strong> | @other</p>
              <p>回复正文不应该当标题。</p>
            </section>
          </section>
        </article>
      </body>
    </html>
    """

    title = OutputHandler._extract_social_first_sentence_title(
        html,
        source_url="https://x.com/lijigang/status/1",
    )

    assert title == "原帖正文第一句。"


def test_extract_metadata_reads_twitter_author_from_meta():
    html = """
    <html>
      <head>
        <title>李继刚 - X Post</title>
        <meta name="surf-source-site" content="twitter">
        <meta name="surf-author" content="@lijigang">
      </head>
      <body>
        <article>
          <h1>李继刚</h1>
          <p>原帖正文第一句。第二句。</p>
        </article>
      </body>
    </html>
    """

    metadata = OutputHandler._extract_metadata(
        html,
        source_url="https://x.com/lijigang/status/1",
    )

    assert metadata["author"] == "@lijigang"
