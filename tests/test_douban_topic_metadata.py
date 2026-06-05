import surf


def test_douban_topic_extracts_author_and_created_metadata():
    html = """
    <html>
    <head><title>Example Topic - Douban</title></head>
    <body>
      <div class="article-main">
        <div class="article-meta">
          <a class="author-name" href="https://www.douban.com/people/example/">Example Author</a>
          <div class="topic-meta">
            <span class="create-time">2026-06-05 14:23:00</span>
          </div>
        </div>
      </div>
    </body>
    </html>
    """

    metadata = surf.OutputHandler._extract_metadata(
        html,
        source_url="https://www.douban.com/topic/123456789/",
    )

    assert metadata["author"] == "Example Author"
    assert metadata["created"] == "2026-06-05T14:23"


def test_douban_topic_extracts_real_world_author_class_selector():
    html = """
    <html>
    <head><title>Example Topic - Douban</title></head>
    <body>
      <div class="article-main">
        <div class="article-meta">
          <a href="https://www.douban.com/people/geekinmedia/" class="author-name">gee</a> 说：
          <div class="topic-meta">
            <span class="create-time">2026-04-22 19:48:13</span>
            <span class="ip-location">北京</span>
          </div>
        </div>
      </div>
    </body>
    </html>
    """

    metadata = surf.OutputHandler._extract_metadata(
        html,
        source_url="https://www.douban.com/topic/484985482/",
    )

    assert metadata["author"] == "gee"
    assert metadata["created"] == "2026-04-22T19:48"


def test_douban_source_strips_tracking_query_params():
    html = """
    <html>
    <head><title>Example Topic - Douban</title></head>
    <body><article><p>Body</p></article></body>
    </html>
    """

    metadata = surf.OutputHandler._extract_metadata(
        html,
        source_url="https://www.douban.com/topic/484985482/?_spm_id=MTUwNDc4MA&_dtcc=1",
    )

    assert metadata["source"] == "https://www.douban.com/topic/484985482/"


def test_douban_source_url_strips_tracking_query():
    html = """
    <html>
      <head><title>Example Topic - Douban</title></head>
      <body><article><p>Body</p></article></body>
    </html>
    """

    metadata = surf.OutputHandler._extract_metadata(
        html,
        source_url="https://www.douban.com/topic/484985482/?_spm_id=MTUwNDc4MA&_dtcc=1",
    )

    assert metadata["source"] == "https://www.douban.com/topic/484985482/"
