"""Второй формат изометрий — российский, из AutoCAD (проект СИБУР).

Здесь всё иначе, чем у Intergraph: текст обычный, шрифтовой, а ведомости швов
нет вообще. Зато на чертёж уже нанесена разметка спулирования, и разбивка
читается прямо с неё:

* подписи спулов (`SP01`, `SP02.1` — уточнение того же спула) стоят у своих
  участков;
* длины отрезков трубы подписаны более мелким шрифтом, чем размерные цепочки:
  у размера «2094» рядом стоит «1910» — это длина реза, за вычетом отводов;
* номера позиций спецификации вынесены на полки выносок.

Каждая такая подпись относится к ближайшему спулу — так и собирается состав.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass

import pymupdf

from .models import SpoolRow
from .pdf_reader import MaterialItem, Sheet

MARKER = "FABRICATION MATERIALS"
SPOOL_RE = re.compile(r"(E-)?SP\s?0*(\d{1,3})(?:\.\d+)?$")
ISO_RE = re.compile(r"\S*\.ISO-\d+")
LINE_RE = re.compile(r"\d{4}-\d{4,6}-[A-Za-z0-9]+-[A-Za-z0-9]+-\d+\s?mm\S*")


@dataclass
class Mark:
    text: str
    x: float
    y: float
    size: float
    vertical: bool


def looks_like_sibur(page) -> bool:
    return MARKER in page.get_text()


def _marks(page) -> list[Mark]:
    out: list[Mark] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            direction = line.get("dir", (1, 0))
            vertical = abs(direction[1]) > 0.95
            for span in line["spans"]:
                text = span["text"].strip()
                if not text:
                    continue
                x0, y0, x1, y1 = span["bbox"]
                out.append(Mark(text, (x0 + x1) / 2, (y0 + y1) / 2,
                                round(span["size"], 1), vertical))
    return out


def _materials(text: str) -> list[MaterialItem]:
    """Спецификации FABRICATION / ERECTION идут подряд обычным текстом."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    items: list[MaterialItem] = []
    erection = False
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("FABRICATION MATERIALS"):
            erection = False
        elif line.startswith("ERECTION MATERIALS"):
            erection = True
        elif re.fullmatch(r"\d{1,3}", line) and i + 1 < len(lines):
            code = lines[i + 1].strip()
            if re.fullmatch(r"[A-Z][A-Z0-9_]{6,}", code):
                j = i + 2
                numeric: list[str] = []
                while j < len(lines) and not re.match(r"[A-Za-z]", lines[j].strip()):
                    if lines[j].strip():
                        numeric.append(lines[j].strip())
                    j += 1
                description = []
                while j < len(lines) and re.match(r"[A-Za-z]", lines[j].strip()):
                    description.append(lines[j].strip())
                    j += 1
                    if len(description) >= 2:
                        break
                tokens = " ".join(numeric).replace("x", " x ").split()
                size, qty = _size_and_qty(tokens)
                items.append(MaterialItem(
                    pt_no=line, size=size, ident=code, qty=qty,
                    description=" ".join(description), erection=erection))
                i = j
                continue
        i += 1
    return items


def _size_and_qty(tokens: list[str]) -> tuple[str, str]:
    if not tokens:
        return "", ""
    if "x" in tokens:
        k = tokens.index("x")
        size = f"{tokens[k - 1]}x{tokens[k + 1]}" if 0 < k < len(tokens) - 1 else tokens[0]
        qty = tokens[-1] if tokens[-1] != size.split("x")[-1] else "1"
        return size, qty
    return tokens[0], tokens[-1]


def _mm(qty: str) -> float | None:
    """«7.2M» -> 7200 мм, «0.1M» -> 100 мм."""
    m = re.fullmatch(r"([\d.,]+)\s*M", qty.strip(), re.I)
    return float(m.group(1).replace(",", ".")) * 1000 if m else None


def _drawing_box(spools: list[Mark], lengths: list[Mark]):
    """Поле чертежа очерчиваем по самим подписям — рамка листа сюда не попадёт."""
    pts = [(m.x, m.y) for m in spools + lengths]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    mx = max((max(xs) - min(xs)) * 0.2, 60)
    my = max((max(ys) - min(ys)) * 0.2, 60)
    return min(xs) - mx, max(xs) + mx, min(ys) - my, max(ys) + my


def _nearest(mark: Mark, spools: list[Mark]) -> str:
    best = min(spools, key=lambda s: math.hypot(s.x - mark.x, s.y - mark.y))
    m = SPOOL_RE.match(best.text.replace(" ", ""))
    return f"{'E-' if m.group(1) else ''}SP{int(m.group(2)):02d}" if m else best.text


def _has_dimension_near(mark: Mark, numeric: list[Mark], radius: float = 60) -> bool:
    """У длины реза рядом всегда стоит размерная цепочка — она крупнее и длиннее."""
    return any(other.size > mark.size
               and math.hypot(other.x - mark.x, other.y - mark.y) < radius
               and float(other.text) >= float(mark.text)
               for other in numeric)


def _main_dn(size: str) -> float | None:
    """Основной диаметр позиции: у бобышки «80x20» это 80."""
    m = re.match(r"\s*([\d.,]+)", size)
    return float(m.group(1).replace(",", ".")) if m else None


def read(page, page_no: int) -> tuple[Sheet, list[SpoolRow]]:
    text = page.get_text()
    sheet = Sheet(page=page_no)
    sheet.fmt = "sibur"

    iso = ISO_RE.search(text)
    if iso:
        sheet.iso = iso.group()
    line = LINE_RE.search(text)
    if line:
        sheet.line = line.group().replace(" ", "")
    rev = re.search(r"Rev\.\s*(\d{1,2})", text)
    sheet.revision = rev.group(1) if rev else "0"

    sheet.materials = _materials(text)
    marks = _marks(page)

    spool_marks = [m for m in marks if SPOOL_RE.match(m.text.replace(" ", ""))]
    if not spool_marks:
        sheet.notes.append(
            "на чертеже нет подписей спулов (SP01, SP02…) — разбивку взять неоткуда")
        return sheet, []

    numeric = [m for m in marks if re.fullmatch(r"\d{2,5}", m.text)]
    sizes = sorted({m.size for m in numeric})
    if len(sizes) < 2:
        sheet.notes.append(
            "не удалось отличить длины отрезков трубы от размерных цепочек — "
            "весь текст размеров одного кегля")
        return sheet, []
    cut_size = sizes[0]                       # длины реза набраны мельче размеров

    lengths = [m for m in numeric if m.size == cut_size]
    box = _drawing_box(spool_marks, lengths)
    if box:
        x0, x1, y0, y1 = box
        inside = lambda m: x0 <= m.x <= x1 and y0 <= m.y <= y1  # noqa: E731
    else:
        inside = lambda m: True                                 # noqa: E731

    # Размеры подписаны вдоль трубы, поэтому вертикальными бывают редко, а вот
    # штамп и рамка набраны вертикально — так и отсеиваются.
    lengths = [m for m in lengths if inside(m)
               and (not m.vertical or _has_dimension_near(m, numeric))]
    balloons = [m for m in marks
                if re.fullmatch(r"\d{1,3}", m.text) and m.vertical
                and m.size > cut_size and inside(m)]

    sheet.marked_spools = sorted({_nearest(m, spool_marks) for m in spool_marks})
    return sheet, _rows(sheet, spool_marks, lengths, balloons)


def _rows(sheet: Sheet, spool_marks: list[Mark], lengths: list[Mark],
          balloons: list[Mark]) -> list[SpoolRow]:
    fabrication = {m.pt_no: m for m in sheet.materials if not m.erection}
    pipes = {no: m for no, m in fabrication.items() if _mm(m.qty) is not None}

    def in_request(no: str) -> bool:
        """Мелочь ниже DN50 в заявку не идёт; бобышка на основной трубе идёт —
        основной диаметр у неё как раз трубный («80x20»)."""
        dn = _main_dn(fabrication[no].size)
        return dn is None or dn >= 50

    # Длины реза раскладываем по спулам, а сами трубы — по позициям спецификации:
    # ровно совпавшую длину отдаём своей позиции, остальное — самой длинной трубе.
    by_spool: dict[str, list[float]] = defaultdict(list)
    for mark in lengths:
        by_spool[_nearest(mark, spool_marks)].append(float(mark.text))

    exact = {round(_mm(m.qty)): no for no, m in pipes.items()}
    main = max(pipes, key=lambda no: _mm(pipes[no].qty)) if pipes else None

    totals: dict[tuple[str, str], float] = defaultdict(float)
    for spool, values in by_spool.items():
        for value in values:
            no = exact.get(round(value), main)
            if no is not None:
                totals[(spool, no)] += value

    for mark in balloons:
        no = mark.text.lstrip("0")
        if no in fabrication and no not in pipes:
            totals[(_nearest(mark, spool_marks), no)] += 1

    rows = []
    for (spool, no), qty in sorted(totals.items()):
        if not in_request(no):
            continue
        item = fabrication[no]
        rows.append(SpoolRow(
            iso=sheet.iso, line=sheet.line, revision=sheet.revision, spool=spool,
            ident=item.ident, qty=qty, unit="мм" if no in pipes else "шт",
            size=item.size, description=item.description))

    _check_totals(sheet, pipes, totals, fabrication, balloons, in_request)
    return rows


def _check_totals(sheet: Sheet, pipes, totals, fabrication, balloons, keep) -> None:
    """Сверка с итогами спецификации — она же ловит недочитанное."""
    for no, item in pipes.items():
        declared = _mm(item.qty) or 0
        got = sum(q for (_, n), q in totals.items() if n == no)
        if declared and abs(got - declared) > max(declared * 0.05, 50):
            sheet.notes.append(
                f"поз. {no} ({item.ident}): по чертежу набралось {got:.0f} мм, "
                f"в спецификации {declared:.0f} мм — часть длин не прочитана")

    seen = {m.text.lstrip("0") for m in balloons}
    for no, item in fabrication.items():
        if no in pipes or no in seen or not keep(no):
            continue
        sheet.notes.append(
            f"поз. {no} ({item.ident}, {item.qty} шт) на чертеже не найдена — "
            "в заявку не попала, впишите вручную")
