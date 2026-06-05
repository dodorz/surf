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
