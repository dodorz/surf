import surf


def test_yaml_frontmatter_title_is_not_quoted():
    frontmatter = surf.OutputHandler._generate_yaml_frontmatter(
        {
            "title": 'An "Example" Title',
            "description": 'A "quoted" description',
            "author": 'Author "Name"',
            "created": None,
            "updated": None,
            "tags": ['tag-one', 'tag "two"'],
            "source": None,
            "archive": None,
            "translator": None,
        }
    )

    assert 'title: An "Example" Title' in frontmatter
    assert 'description: A "quoted" description' in frontmatter
    assert 'author: Author "Name"' in frontmatter
    assert "  - tag-one" in frontmatter
    assert '  - tag "two"' in frontmatter


def test_yaml_frontmatter_title_and_description_are_single_line():
    frontmatter = surf.OutputHandler._generate_yaml_frontmatter(
        {
            "title": "  Line one\nline two\t\twith   spaces  ",
            "description": "First line\r\nSecond line\u00a0with\u200b hidden space",
            "created": None,
            "updated": None,
            "tags": [],
            "source": None,
            "archive": None,
            "translator": None,
        }
    )

    lines = frontmatter.splitlines()
    assert "title: Line one line two with spaces" in lines
    assert "description: First line Second line with hidden space" in lines
    assert all("\t" not in line and "\u00a0" not in line and "\u200b" not in line for line in lines)


def test_yaml_frontmatter_drops_empty_title_and_description():
    frontmatter = surf.OutputHandler._generate_yaml_frontmatter(
        {
            "title": "   \n ",
            "description": "\n\t ",
            "created": None,
            "updated": None,
            "tags": [],
            "source": None,
            "archive": None,
            "translator": None,
        }
    )

    assert "title:" not in frontmatter
    assert "description:" not in frontmatter


def test_yaml_frontmatter_removes_spaces_between_cjk_characters():
    frontmatter = surf.OutputHandler._generate_yaml_frontmatter(
        {
            "title": "为了制作\n《侠盗猎车手6》，\n工作室首先必须重建自己。",
            "description": "第一行\r\n第二行，\u00a0第三行",
            "created": None,
            "updated": None,
            "tags": [],
            "source": None,
            "archive": None,
            "translator": None,
        }
    )

    assert "title: 为了制作《侠盗猎车手6》，工作室首先必须重建自己" in frontmatter
    assert "description: 第一行第二行，第三行" in frontmatter


def test_yaml_frontmatter_title_drops_trailing_period():
    frontmatter = surf.OutputHandler._generate_yaml_frontmatter(
        {
            "title": "Example Title.",
            "description": "Example description.",
            "created": None,
            "updated": None,
            "tags": [],
            "source": None,
            "archive": None,
            "translator": None,
        }
    )

    assert "title: Example Title" in frontmatter
    # The description keeps its own sentence-ending period.
    assert "description: Example description." in frontmatter


def test_yaml_frontmatter_title_keeps_ellipsis():
    frontmatter = surf.OutputHandler._generate_yaml_frontmatter(
        {
            "title": "Wait for it...",
            "description": None,
            "created": None,
            "updated": None,
            "tags": [],
            "source": None,
            "archive": None,
            "translator": None,
        }
    )

    assert "title: Wait for it..." in frontmatter


def test_yaml_frontmatter_drops_github_contribute_boilerplate():
    boilerplate = "Contribute to owner/repo development by creating an account on GitHub."
    frontmatter = surf.OutputHandler._generate_yaml_frontmatter(
        {
            "title": f"owner/repo: A short summary. {boilerplate}",
            "description": f"A short summary. {boilerplate}",
            "created": None,
            "updated": None,
            "tags": [],
            "source": None,
            "archive": None,
            "translator": None,
        }
    )

    assert "GitHub. Contribute" not in frontmatter
    assert "Contribute to" not in frontmatter
    assert "title: owner/repo: A short summary" in frontmatter
    assert "description: A short summary." in frontmatter


def test_yaml_frontmatter_drops_github_contribute_only_description():
    frontmatter = surf.OutputHandler._generate_yaml_frontmatter(
        {
            "title": "owner/repo",
            "description": "Contribute to owner/repo development by creating an account on GitHub.",
            "created": None,
            "updated": None,
            "tags": [],
            "source": None,
            "archive": None,
            "translator": None,
        }
    )

    assert "description:" not in frontmatter
    assert "title: owner/repo" in frontmatter
