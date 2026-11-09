import surf
from surf import OcrHandler


def _box(left, top, right, bottom):
    """Axis-aligned detector box in the (x, y) order OCR engines emit."""
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def test_wrapped_lines_join_into_one_paragraph():
    # A sentence wrapped over three visual lines: tight gaps, same left edge.
    entries = [
        ("之前描述过这套家庭服务器的设置。这套", _box(74, 92, 819, 132)),
        ("Homelab仍由Mac mini、T630和VPS组成。近", _box(74, 165, 975, 204)),
        ("期没有更换核心硬件。", _box(75, 238, 296, 277)),
    ]
    assert OcrHandler._assemble_ocr_paragraphs(entries) == (
        "之前描述过这套家庭服务器的设置。这套Homelab仍由Mac mini、T630和VPS组成。近"
        "期没有更换核心硬件。"
    )


def test_extra_leading_starts_a_new_paragraph():
    entries = [
        ("上一段的最后一行内容。", _box(74, 92, 900, 132)),
        ("下一段的第一行内容。", _box(74, 196, 900, 236)),
    ]
    assert OcrHandler._assemble_ocr_paragraphs(entries) == (
        "上一段的最后一行内容。\n\n下一段的第一行内容。"
    )


def test_indented_first_line_starts_a_new_paragraph():
    entries = [
        ("短行结束上一段。", _box(74, 92, 300, 132)),
        ("缩进的新段落。", _box(150, 165, 900, 205)),
    ]
    assert OcrHandler._assemble_ocr_paragraphs(entries) == (
        "短行结束上一段。\n\n缩进的新段落。"
    )


def test_heading_lines_join_but_body_does_not_merge_into_heading():
    entries = [
        ("二、 Mac mini:", _box(77, 1077, 589, 1143)),
        ("存储与家庭共享", _box(75, 1169, 568, 1232)),
        ("Macmini配置16GB内存。", _box(71, 1309, 1005, 1350)),
    ]
    assert OcrHandler._assemble_ocr_paragraphs(entries) == (
        "二、 Mac mini:存储与家庭共享\n\nMacmini配置16GB内存。"
    )


def test_latin_wrap_keeps_a_space():
    entries = [
        ("The quick brown", _box(70, 100, 900, 140)),
        ("fox jumps over", _box(70, 172, 900, 212)),
        ("the lazy dog.", _box(70, 244, 400, 284)),
    ]
    assert (
        OcrHandler._assemble_ocr_paragraphs(entries)
        == "The quick brown fox jumps over the lazy dog."
    )


def test_no_space_before_cjk_punctuation():
    entries = [
        ("配置了 PoE 交换机", _box(70, 100, 900, 140)),
        ("，其余不变。", _box(70, 172, 400, 212)),
    ]
    assert OcrHandler._assemble_ocr_paragraphs(entries) == "配置了 PoE 交换机，其余不变。"


def test_entries_without_geometry_keep_line_breaks():
    entries = [("第一行", None), ("第二行", None)]
    assert OcrHandler._assemble_ocr_paragraphs(entries) == "第一行\n\n第二行"


def test_empty_entries_return_empty_text():
    assert OcrHandler._assemble_ocr_paragraphs([]) == ""
    assert OcrHandler._assemble_ocr_paragraphs([("  ", None)]) == ""


def test_ocr_block_emits_one_paragraph_per_blank_line_separated_block():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup("<div></div>", "html.parser")
    block = OcrHandler._build_ocr_block(soup, "第一段第一句。第一段第二句。\n\n第二段。")
    paragraphs = block.find_all("p")
    assert paragraphs[0].get_text(strip=True) == "OCR Text"
    assert [p.get_text() for p in paragraphs[1:]] == [
        "第一段第一句。第一段第二句。",
        "第二段。",
    ]


def test_plain_text_keeps_engine_paragraphs_and_joins_wrapped_lines():
    # Tesseract-style output: wrapped lines, blank line between paragraphs.
    text = (
        "之前描述过这套家庭服务器的设置。这套\n"
        "Homelab仍由Mac mini、T630和VPS组成。\n"
        "\n"
        "调整的重点是明确数据归属。\n"
    )
    assert OcrHandler._normalize_ocr_blocks(text) == (
        "之前描述过这套家庭服务器的设置。这套Homelab仍由Mac mini、T630和VPS组成。\n\n"
        "调整的重点是明确数据归属。"
    )


def test_plain_text_without_blank_lines_keeps_line_structure():
    assert OcrHandler._normalize_ocr_blocks("第一行\n第二行") == "第一行\n第二行"


def test_plain_text_latin_paragraph_keeps_spaces():
    text = "The quick brown\nfox jumps over\n\nthe lazy dog.\n"
    assert OcrHandler._normalize_ocr_blocks(text) == (
        "The quick brown fox jumps over\n\nthe lazy dog."
    )
