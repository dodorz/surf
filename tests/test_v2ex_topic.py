import surf


V2EX_HTML = """
<html>
<head><title>Example - V2EX</title></head>
<body>
<div id="Main">
  <div class="box">
    <div class="header">
      <div class="flex-one-row"><div><a href="/">V2EX</a><a href="/go/test">Test Node</a></div></div>
      <h1>Example Topic</h1>
      <small class="gray"><a href="/member/alice">alice</a> · <span title="2026-04-24 18:51:29 +08:00">1 day ago</span></small>
    </div>
    <div class="cell">
      <div class="topic_content"><div class="markdown_body"><p>Main post body.</p></div></div>
    </div>
  </div>
  <div id="r_1" class="cell">
    <strong><a href="/member/bob" class="dark">bob</a></strong>
    <span class="ago" title="2026-04-24 19:00:00 +08:00">1 day ago</span>
    <span class="no">1</span>
    <div class="reply_content"><p>Reply body.</p></div>
  </div>
</div>
</body>
</html>
"""


def test_v2ex_topic_default_extracts_main_post_without_replies():
    page = surf.Fetcher._extract_v2ex_topic_page(
        V2EX_HTML,
        "https://v2ex.com/t/1",
        include_replies=False,
    )

    assert page["title"] == "Example Topic"
    markdown = "\n".join(page["markdown_lines"])
    assert "Author: alice" in markdown
    assert "Node: Test Node" in markdown
    assert "Main post body." in markdown
    assert page["replies"] == []


def test_v2ex_topic_can_extract_replies_when_requested():
    page = surf.Fetcher._extract_v2ex_topic_page(
        V2EX_HTML,
        "https://v2ex.com/t/1",
        include_replies=True,
    )

    assert page["replies"] == [
        {
            "floor": "1",
            "author": "bob",
            "time": "2026-04-24 19:00:00 +08:00",
            "markdown": "Reply body.",
        }
    ]


def test_thread_flag_accepts_url_as_next_argument():
    assert surf._normalize_thread_argv(["-t", "https://v2ex.com/t/1"]) == [
        "-t",
        "backward",
        "https://v2ex.com/t/1",
    ]
