"""Smoke-test PaddleOCR integration in OcrHandler."""
import pytest
from unittest.mock import MagicMock, patch


class FakeArgs:
    def __init__(self, ocr_engine="rapidocr", ocr_lang=None, ocr=False, no_ocr=False):
        self.ocr_engine = ocr_engine
        self.ocr_lang = ocr_lang
        self.ocr = ocr
        self.no_ocr = no_ocr


class FakeConfig:
    def get(self, section, key, fallback=""):
        return fallback


def test_engine_chain_paddleocr_only():
    from surf import OcrHandler
    chain = OcrHandler._get_engine_chain(FakeArgs(ocr_engine="paddleocr"), FakeConfig())
    assert chain == ["paddleocr"]


def test_engine_chain_rapidocr():
    from surf import OcrHandler
    chain = OcrHandler._get_engine_chain(FakeArgs(ocr_engine="rapidocr"), FakeConfig())
    assert chain == ["rapidocr", "tesseract"]


def test_engine_chain_tesseract():
    from surf import OcrHandler
    chain = OcrHandler._get_engine_chain(FakeArgs(ocr_engine="tesseract"), FakeConfig())
    assert chain == ["tesseract"]


def test_engine_chain_auto():
    from surf import OcrHandler
    chain = OcrHandler._get_engine_chain(FakeArgs(ocr_engine="auto"), FakeConfig())
    assert chain == ["paddleocr", "rapidocr", "tesseract"]


def test_engine_chain_default_is_rapidocr():
    from surf import OcrHandler
    chain = OcrHandler._get_engine_chain(FakeArgs(ocr_engine=None), FakeConfig())
    assert chain == ["rapidocr", "tesseract"]


def test_engine_chain_unknown_falls_back():
    from surf import OcrHandler
    chain = OcrHandler._get_engine_chain(FakeArgs(ocr_engine="unknown_xyz"), FakeConfig())
    assert chain == ["rapidocr", "tesseract"]


def test_runtime_inits_paddleocr_if_available():
    from surf import OcrHandler
    args = FakeArgs(ocr_engine="paddleocr", ocr_lang=None)
    cfg = FakeConfig()

    with patch.object(OcrHandler, "_init_paddleocr", return_value="mock_paddle"):
        with patch.object(OcrHandler, "_init_rapidocr", return_value="mock_rapid"):
            with patch.object(OcrHandler, "_init_tesseract", return_value="mock_tess"):
                runtime = OcrHandler._create_ocr_runtime(args, cfg)

    assert runtime["chain"] == ["paddleocr"]
    assert "paddleocr" in runtime["available"]
    assert runtime["available"]["paddleocr"] == "mock_paddle"


def test_runtime_auto_inits_all_available():
    from surf import OcrHandler
    args = FakeArgs(ocr_engine="auto", ocr_lang=None)
    cfg = FakeConfig()

    with patch.object(OcrHandler, "_init_paddleocr", return_value="mock_paddle"):
        with patch.object(OcrHandler, "_init_rapidocr", return_value="mock_rapid"):
            with patch.object(OcrHandler, "_init_tesseract", return_value="mock_tess"):
                runtime = OcrHandler._create_ocr_runtime(args, cfg)

    assert runtime["chain"] == ["paddleocr", "rapidocr", "tesseract"]
    assert set(runtime["available"].keys()) == {"paddleocr", "rapidocr", "tesseract"}


def test_run_ocr_uses_paddleocr_first_in_auto_chain():
    from surf import OcrHandler
    from PIL import Image as PilImage
    img = PilImage.new("RGB", (100, 100), color="white")
    prepared = OcrHandler._prepare_image_for_ocr(img)

    runtime = {
        "chain": ["paddleocr", "rapidocr"],
        "available": {
            "paddleocr": "mock_paddle_engine",
            "rapidocr": "mock_rapid_engine",
        },
    }

    with patch.object(OcrHandler, "_extract_text_with_paddleocr", return_value="hello paddle"):
        text, engine = OcrHandler._run_ocr_with_engines(runtime, prepared, "test.png")

    assert text == "hello paddle"
    assert engine == "paddleocr"


def test_run_ocr_falls_back_when_paddleocr_fails():
    from surf import OcrHandler
    from PIL import Image as PilImage
    img = PilImage.new("RGB", (100, 100), color="white")
    prepared = OcrHandler._prepare_image_for_ocr(img)

    runtime = {
        "chain": ["paddleocr", "rapidocr"],
        "available": {
            "paddleocr": "mock_paddle_engine",
            "rapidocr": "mock_rapid_engine",
        },
    }

    with patch.object(OcrHandler, "_extract_text_with_paddleocr") as mock_p:
        mock_p.side_effect = RuntimeError("PaddleOCR crashed")
        with patch.object(OcrHandler, "_extract_text_with_rapidocr", return_value="hello rapid"):
            text, engine = OcrHandler._run_ocr_with_engines(runtime, prepared, "test.png")

    assert text == "hello rapid"
    assert engine == "rapidocr"


def test_paddleocr_in_valid_engine_settings():
    from surf import OcrHandler
    value = OcrHandler._get_engine_setting(FakeArgs(ocr_engine="paddleocr"), FakeConfig())
    assert value == "paddleocr"