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
        "after",
        "https://v2ex.com/t/1",
    ]


def test_v2ex_source_url_drops_reply_fragment_for_front_matter():
    html = surf._build_direct_markdown_payload(
        "Body",
        title="Example Topic",
        source_url="https://v2ex.com/t/123#reply7",
        site_name="v2ex",
    )

    metadata = surf.OutputHandler._extract_metadata(html, source_url="https://v2ex.com/t/123#reply7")

    assert metadata["source"] == "https://v2ex.com/t/123"


def test_v2ex_metadata_moves_leading_fields_into_front_matter():
    html = surf._build_direct_markdown_payload(
        "Author: TuTouPower\nNode: 程序员\nPublished: 2026-05-27 09:04:11 +08:00\nSource: https://v2ex.com/t/1215784#reply106\n\nBody",
        title="Example Topic",
        source_url="https://v2ex.com/t/1215784#reply106",
        site_name="v2ex",
    )

    metadata = surf.OutputHandler._extract_metadata(html, source_url="https://v2ex.com/t/1215784#reply106")

    assert metadata["author"] == "TuTouPower"
    assert metadata["tags"] == ["程序员"]
    assert metadata["created"] == "2026-05-27T09:04"
    assert metadata["source"] == "https://v2ex.com/t/1215784"


def test_strip_v2ex_leading_metadata_removes_header_block_from_body():
    markdown = (
        "Author: TuTouPower\n"
        "Node: 程序员\n"
        "Published: 2026-05-27 09:04:11 +08:00\n"
        "Source: https://v2ex.com/t/1215784#reply106\n\n"
        "Body\n\nMore"
    )

    stripped = surf._strip_v2ex_leading_metadata(markdown)

    assert stripped == "Body\n\nMore"


def test_v2ex_source_url_keeps_non_reply_fragment():
    html = surf._build_direct_markdown_payload(
        "Body",
        title="Example Topic",
        source_url="https://v2ex.com/t/123#other",
        site_name="v2ex",
    )

    metadata = surf.OutputHandler._extract_metadata(html, source_url="https://v2ex.com/t/123#other")

    assert metadata["source"] == "https://v2ex.com/t/123#other"


def test_v2ex_source_url_drops_reply_fragment_case_insensitively():
    html = surf._build_direct_markdown_payload(
        "Body",
        title="Example Topic",
        source_url="https://v2ex.com/t/123#Reply0",
        site_name="v2ex",
    )

    metadata = surf.OutputHandler._extract_metadata(html, source_url="https://v2ex.com/t/123#Reply0")

    assert metadata["source"] == "https://v2ex.com/t/123"


def test_v2ex_html_page_metadata_moves_into_front_matter():
    metadata = surf.OutputHandler._extract_metadata(
        V2EX_HTML,
        source_url="https://v2ex.com/t/1#Reply0",
    )

    assert metadata["author"] == "alice"
    assert metadata["tags"] == ["Test Node"]
    assert metadata["created"] == "2026-04-24T18:51"
    assert metadata["source"] == "https://v2ex.com/t/1"

def test_thread_extraction_supports_range_and_author_scope():
    items = [
        {"author_key": "alice", "text": "previous alice"},
        {"author_key": "bob", "text": "previous bob"},
        {"author_key": "alice", "text": "current"},
        {"author_key": "bob", "text": "reply bob"},
        {"author_key": "alice", "text": "reply alice"},
    ]

    same_items, same_index = surf.Fetcher._extract_thread_items(items, 2, "both", "same")
    assert same_items == [items[2]]
    assert same_index == 0

    all_items, all_index = surf.Fetcher._extract_thread_items(items, 2, "both", "all")
    assert all_items == items
    assert all_index == 2
