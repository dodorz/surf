#!/usr/bin/env python3
"""Short-lived OCR worker for Surf.

Surf downloads and preprocesses article images, then hands the prepared PNGs
to this separate process instead of loading PaddleOCR / RapidOCR / Tesseract
inside the long-lived WSGI worker. The heavy engines routinely map hundreds of
megabytes; because this process exits after every batch, that memory is
returned to the OS instead of accumulating in the web worker.

Protocol: one JSON object on stdin, one JSON object on stdout.
  in : {"engine": "auto", "lang": "chi_sim+eng", "tesseract_cmd": "",
        "images": [{"url": "...", "png": "<base64 of prepared PNG>"}, ...]}
  out: {"results": [{"text": "...", "engine": "rapidocr"}, ...], "error": null}

All diagnostic output is redirected to stderr so stdout stays valid JSON.
"""
import base64
import io
import json
import sys
import types


class _WorkerConfig:
    """Minimal ``surf.Config`` look-alike for standalone OCR engine init."""

    def __init__(self, lang="", tesseract_cmd=""):
        self._values = {
            "lang": lang or "chi_sim+eng",
            "tesseract_cmd": tesseract_cmd or "",
        }

    def get(self, section, key, fallback=None):
        return self._values.get(key, fallback)

    def get_path(self, section, key, fallback=None):
        value = self._values.get(key, fallback)
        return value if value is not None else fallback

    def has_section(self, section):
        return True


def _build_runtime(payload):
    # Imported here so an unavailable Surf install still produces a clean JSON
    # error instead of an import traceback on stdout.
    from surf import OcrHandler

    args = types.SimpleNamespace(
        ocr_engine=payload.get("engine") or "auto",
        ocr_lang=payload.get("lang") or "",
    )
    config = _WorkerConfig(
        lang=payload.get("lang") or "",
        tesseract_cmd=payload.get("tesseract_cmd") or "",
    )
    return OcrHandler, OcrHandler._create_ocr_runtime(args, config)


def main():
    real_stdout = sys.stdout
    # PaddleOCR/RapidOCR/ONNX print banners directly to stdout; keep stdout
    # reserved for the single JSON response.
    sys.stdout = sys.stderr

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception as exc:
        json.dump({"results": [], "error": f"invalid job: {exc}"}, real_stdout)
        return 1

    try:
        OcrHandler, runtime = _build_runtime(payload)
    except Exception as exc:
        json.dump({"results": [], "error": f"worker init failed: {exc}"}, real_stdout)
        return 1

    results = []
    if not runtime.get("available"):
        json.dump({"results": results, "error": "no OCR engine available"}, real_stdout)
        return 0

    from PIL import Image  # type: ignore

    for item in payload.get("images", []):
        try:
            image = Image.open(io.BytesIO(base64.b64decode(item["png"])))
            image.load()
            text, engine = OcrHandler._run_ocr_with_engines(runtime, image, item.get("url", ""))
            results.append({"text": text, "engine": engine})
        except Exception as exc:  # keep going: one bad image must not sink the batch
            results.append({"text": "", "engine": None, "error": str(exc)})

    json.dump({"results": results, "error": None}, real_stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
