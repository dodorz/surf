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

    assert "title: 为了制作《侠盗猎车手6》，工作室首先必须重建自己。" in frontmatter
    assert "description: 第一行第二行，第三行" in frontmatter
