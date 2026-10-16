"""OCR for scanned PDFs: pypdfium2 renders a page to an image, RapidOCR (ONNX, CPU, models bundled in the wheel)
reads it. Everything local - no API key, no system install. If the packages are missing, `available()` is False
and scanned PDFs fail with a clear message instead of crashing."""
import importlib.util
import logging
import os
import threading

import numpy as np

log = logging.getLogger("ocr")
MIN_SCORE = 0.5                                  # drop lines the engine itself is unsure about
MAX_PAGES = int(os.environ.get("OCR_MAX_PAGES", "150"))  # cost guard: ~1.5 s per page on CPU
MAX_SIDE = 2600                                  # px; bounds memory for huge pages
_lock = threading.Lock()
_engine = None
_failed = False


def available() -> bool:
    return (not _failed and importlib.util.find_spec("rapidocr_onnxruntime") is not None
            and importlib.util.find_spec("pypdfium2") is not None)


def _load():
    global _engine, _failed
    with _lock:
        if _engine is None and not _failed:
            try:
                from rapidocr_onnxruntime import RapidOCR
                _engine = RapidOCR()
            except Exception:
                log.exception("OCR engine failed to load")
                _failed = True
    return _engine


def recognize(img) -> str:
    """Text of one PIL image, lines in reading order."""
    eng = _load()
    if eng is None:
        return ""
    with _lock:  # one ONNX session shared by all worker threads
        res, _ = eng(np.array(img.convert("RGB")))
    return "\n".join(t.strip() for _, t, score in (res or []) if float(score) >= MIN_SCORE and t.strip())


def render_pages(path, indices):
    """Yield (page_index, PIL image) for the given 0-based pages."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(path)
    try:
        for i in indices:
            page = pdf[i]
            try:
                w, h = page.get_size()  # points
                yield i, page.render(scale=min(2.0, MAX_SIDE / max(w, h))).to_pil()
            finally:
                page.close()
    finally:
        pdf.close()
