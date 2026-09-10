"""Распознавание того, что в PDF нарисовано, а не написано.

На части листов таблицы вставлены картинкой, а подписи на чертеже выведены
кривыми — обычным чтением текста их не достать. Здесь лист превращается в
картинку и распознаётся.

Движок ставится отдельно (`pip install rapidocr-onnxruntime`); если его нет,
программа работает как раньше, только без этих данных.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import pymupdf

ZOOM = 3.0                       # 216 dpi: мельче — цифры начинают путаться


@dataclass
class Word:
    text: str
    x: float                     # в координатах страницы PDF
    y: float
    confidence: float


INSTALL_HINT = ("pip install rapidocr-onnxruntime "
                "(если откажется из-за версии Python — тот же вызов "
                "с ключом --ignore-requires-python)")


@lru_cache(maxsize=1)
def _engine():
    """Движок распознавания, если он установлен.

    У пакета два имени: старое `rapidocr_onnxruntime` и новое `rapidocr`.
    Берём любой, какой найдётся.
    """
    for module in ("rapidocr_onnxruntime", "rapidocr"):
        try:
            RapidOCR = __import__(module, fromlist=["RapidOCR"]).RapidOCR
        except (ImportError, AttributeError):
            continue
        try:
            return RapidOCR()
        except Exception:                     # движок есть, но не поднялся
            continue
    return None


def available() -> bool:
    return _engine() is not None


def _run(image_bytes: bytes, scale_x: float, scale_y: float,
         offset_x: float = 0.0, offset_y: float = 0.0) -> list[Word]:
    engine = _engine()
    if engine is None:
        return []
    # Картинку отдаём движку байтами как есть: после пересборки через
    # промежуточные библиотеки часть мелких подписей перестаёт находиться.
    outcome = engine(image_bytes)
    result = _as_list(outcome)
    words = []
    for box, text, confidence in (result or []):
        x = sum(p[0] for p in box) / 4
        y = sum(p[1] for p in box) / 4
        words.append(Word(text.strip(), offset_x + x / scale_x,
                          offset_y + y / scale_y, float(confidence)))
    return words


def _as_list(outcome):
    """Привести ответ движка к общему виду: старый отдаёт кортеж, новый — объект."""
    if outcome is None:
        return []
    if isinstance(outcome, tuple):
        return outcome[0] or []
    boxes = getattr(outcome, "boxes", None)
    if boxes is None:
        return outcome or []
    texts = getattr(outcome, "txts", None) or []
    scores = getattr(outcome, "scores", None) or []
    return [(box, text, score)
            for box, text, score in zip(boxes, texts, scores)]


def read_page(page) -> list[Word]:
    """Распознать лист целиком — вернуть подписи в координатах страницы."""
    if not available():
        return []
    pix = page.get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM))
    return _run(pix.tobytes("png"), ZOOM, ZOOM)


def read_images(page) -> list[tuple[pymupdf.Rect, list[Word]]]:
    """Распознать каждую вставленную картинку отдельно — так точнее таблицы."""
    if not available():
        return []
    doc = page.parent
    out = []
    for info in page.get_image_info(xrefs=True):
        xref = info.get("xref")
        if not xref:
            continue
        rect = pymupdf.Rect(info["bbox"])
        if rect.width < 40 or rect.height < 40:
            continue                              # логотипы и печати не в счёт
        data = doc.extract_image(xref)
        scale_x = data["width"] / rect.width if rect.width else 1
        scale_y = data["height"] / rect.height if rect.height else 1
        out.append((rect, _run(data["image"], scale_x, scale_y, rect.x0, rect.y0)))
    return out


def rows(words: list[Word], tolerance: float = 6.0) -> list[list[Word]]:
    """Собрать распознанные подписи в строки таблицы."""
    ordered = sorted(words, key=lambda w: (w.y, w.x))
    out: list[list[Word]] = []
    for word in ordered:
        if out and abs(out[-1][0].y - word.y) <= tolerance:
            out[-1].append(word)
        else:
            out.append([word])
    return [sorted(r, key=lambda w: w.x) for r in out]


PIECE_RE = re.compile(r"<\s*(\d{1,3})\s*[>»vV]?$")


def piece_number(text: str) -> str | None:
    """«<12>» и кривые прочтения вроде «<12v» — это катушка №12."""
    m = PIECE_RE.search(text.replace(" ", ""))
    return m.group(1) if m else None
