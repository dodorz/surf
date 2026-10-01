import argparse
import configparser

from surf import OcrHandler


def _config(**options):
    config = configparser.ConfigParser()
    config.add_section("OCR")
    for key, value in options.items():
        config.set("OCR", key, value)
    return config


def test_engine_setting_defaults_to_auto():
    args = argparse.Namespace(ocr_engine=None)
    assert OcrHandler._get_engine_setting(args, _config()) == "auto"


def test_engine_chain_for_auto_default():
    args = argparse.Namespace(ocr_engine=None)
    assert OcrHandler._get_engine_chain(args, _config()) == [
        "paddleocr",
        "rapidocr",
        "tesseract",
    ]


def test_config_value_overrides_auto_default():
    args = argparse.Namespace(ocr_engine=None)
    assert OcrHandler._get_engine_setting(args, _config(engine="rapidocr")) == "rapidocr"
    assert OcrHandler._get_engine_chain(args, _config(engine="rapidocr")) == [
        "rapidocr",
        "tesseract",
    ]


def test_cli_argument_overrides_config():
    args = argparse.Namespace(ocr_engine="tesseract")
    assert OcrHandler._get_engine_setting(args, _config(engine="auto")) == "tesseract"
    assert OcrHandler._get_engine_chain(args, _config(engine="auto")) == ["tesseract"]


def test_unknown_engine_falls_back_to_auto_chain():
    args = argparse.Namespace(ocr_engine=None)
    assert OcrHandler._get_engine_chain(args, _config(engine="bogus")) == [
        "paddleocr",
        "rapidocr",
        "tesseract",
    ]
