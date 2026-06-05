import surf


def test_build_zhihu_html_moves_metadata_out_of_body():
    html = surf.Fetcher._build_zhihu_html(
        title="Answer Title",
        content_html="<p>Body</p>",
        source_url="https://www.zhihu.com/question/1/answer/2",
        author_name="Alice",
        question_title="Question Title",
        created_time="2026-06-05 10:20:30",
        updated_time="2026-06-05 12:34:56",
        voteup_count=123,
        comment_count=45,
    )

    assert html is not None
    assert "Question: Question Title" in html
    assert "Author: Alice" not in html
    assert "Created: 2026-06-05 10:20:30" not in html
    assert "Updated: 2026-06-05 12:34:56" not in html
    assert "Upvotes: 123" not in html
    assert "Comments: 45" not in html
    assert "https://www.zhihu.com/question/1/answer/2</a>" not in html
    assert "name='surf-source-site'" in html
    assert "content='zhihu'" in html
    assert "name='surf-author'" in html
    assert "content='Alice'" in html
    assert "name='surf-created'" in html
    assert "content='2026-06-05 10:20:30'" in html
    assert "name='surf-updated'" in html
    assert "content='2026-06-05 12:34:56'" in html


def test_extract_metadata_reads_zhihu_meta_fields():
    html = """
    <html>
      <head>
        <title>Answer Title</title>
        <meta name="surf-source-site" content="zhihu">
        <meta name="surf-author" content="Alice">
        <meta name="surf-created" content="2026-06-05 10:20:30">
        <meta name="surf-updated" content="2026-06-05 12:34:56">
      </head>
      <body>
        <article>
          <h1>Answer Title</h1>
          <p>Body</p>
        </article>
      </body>
    </html>
    """

    metadata = surf.OutputHandler._extract_metadata(
        html,
        source_url="https://www.zhihu.com/question/1/answer/2",
    )

    assert metadata["author"] == "Alice"
    assert metadata["created"] == "2026-06-05T10:20"
    assert metadata["updated"] == "2026-06-05T12:34"
