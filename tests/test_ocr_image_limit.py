import argparse
import configparser
import io

import surf
from surf import OcrHandler

RAW_NOTE = """
<html><head><meta charset="utf-8"><title>t</title>
<meta name="surf-source-site" content="xiaohongshu"></head>
<body><article><h1>t</h1>
<div class="images">
<img src="https://img.example/a.jpg"/>
<img src="https://img.example/b.jpg"/>
<img src="https://img.example/c.jpg"/>
<img src="https://img.example/d.jpg"/>
<img src="https://img.example/e.jpg"/>
</div><p>body</p></article></body></html>
"""


def _config(**options):
    config = configparser.ConfigParser()
    config.add_section("OCR")
    for key, value in options.items():
        config.set("OCR", key, value)
    return config


def _args(**overrides):
    values = dict(ocr=True, no_ocr=False, ocr_engine="rapidocr", ocr_lang=None, ocr_max_images=None)
    values.update(overrides)
    return argparse.Namespace(**values)


def _install_stubs(monkeypatch):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("L", (600, 300), 255).save(buffer, format="PNG")
    png = buffer.getvalue()

    monkeypatch.setattr(OcrHandler, "_download_image", staticmethod(lambda *a, **k: png))
    monkeypatch.setattr(OcrHandler, "_any_engine_installed", staticmethod(lambda chain: True))
    monkeypatch.setattr(surf.Fetcher, "_get_proxies", staticmethod(lambda *a, **k: ({}, None)))
    monkeypatch.setattr(
        OcrHandler,
        "_run_ocr_batch_in_worker",
        staticmethod(lambda candidates, args, config: ([("recognised text", "rapidocr")] * len(candidates), None)),
    )


def test_config_max_images_caps_the_number_of_ocr_blocks(monkeypatch):
    _install_stubs(monkeypatch)
    html = OcrHandler.annotate_html_with_ocr(
        RAW_NOTE, "https://www.xiaohongshu.com/", "xiaohongshu", None, _args(), _config(max_images="2")
    )
    assert html.count('class="surf-ocr"') == 2


def test_cli_override_replaces_the_config_limit(monkeypatch):
    _install_stubs(monkeypatch)
    html = OcrHandler.annotate_html_with_ocr(
        RAW_NOTE,
        "https://www.xiaohongshu.com/",
        "xiaohongshu",
        None,
        _args(ocr_max_images=4),
        _config(max_images="1"),
    )
    assert html.count('class="surf-ocr"') == 4


def test_zero_limit_ocrs_every_image(monkeypatch):
    _install_stubs(monkeypatch)
    html = OcrHandler.annotate_html_with_ocr(
        RAW_NOTE,
        "https://www.xiaohongshu.com/",
        "xiaohongshu",
        None,
        _args(ocr_max_images=0),
        _config(max_images="2"),
    )
    assert html.count('class="surf-ocr"') == 5
