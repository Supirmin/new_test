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


def _annotation_marks(page) -> list[Mark]:
    """AutoCAD кладёт часть подписей не в текст, а в аннотации SHX Text.

    На одних листах подписи спулов — обычный текст, на других — аннотации;
    читать надо и то и другое. Кегля у аннотации нет, поэтому берём высоту
    прямоугольника: у повёрнутой подписи это её ширина.
    """
    out: list[Mark] = []
    for a in page.annots() or []:
        text = a.info.get("content", "").strip()
        if not text:
            continue
        r = a.rect
        vertical = r.height > r.width
        out.append(Mark(text, (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2,
                        round(r.width if vertical else r.height, 1), vertical))
    return out


LEGEND_WORDS = ("спул", "шов", "катушк", "Условные")


def _drop_legend(marks: list[Mark], radius: float = 110) -> list[Mark]:
    """Убрать образцы из «Условных обозначений».

    В легенде нарисованы такие же подписи — SP01, S1, <1>, — и если принять их
    за настоящие, все ближайшие позиции уедут в несуществующий спул.
    """
    anchors = [m for m in marks
               if any(w.lower() in m.text.lower() for w in LEGEND_WORDS)]
    if not anchors:
        return marks
    return [m for m in marks
            if m in anchors
            or all(math.hypot(m.x - a.x, m.y - a.y) > radius for a in anchors)]


def _marks(page) -> list[Mark]:
    out: list[Mark] = _annotation_marks(page)
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


def _drawing_box(marks: list[Mark], margin: float = 0.2):
    """Поле чертежа очерчиваем по самим подписям — штамп и рамка сюда не попадут."""
    if not marks:
        return None
    xs = [m.x for m in marks]
    ys = [m.y for m in marks]
    mx = max((max(xs) - min(xs)) * margin, 60)
    my = max((max(ys) - min(ys)) * margin, 60)
    return min(xs) - mx, max(xs) + mx, min(ys) - my, max(ys) + my


def _inside(box):
    if box is None:
        return lambda m: True
    x0, x1, y0, y1 = box
    return lambda m: x0 <= m.x <= x1 and y0 <= m.y <= y1


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


def _cut_lengths(numeric: list[Mark], skip: set[str] = frozenset()) -> tuple[list[Mark], float]:
    """Отделить длины реза от размерных цепочек.

    На одних листах длины набраны мельче размеров — тогда достаточно кегля.
    На других весь текст одного кегля, и различает только расположение: длина
    реза подписана вплотную к своему размеру и всегда меньше его, потому что
    из размера вычтены отводы. В такой связке длиной реза считается наименьшее
    число.
    """
    numeric = [m for m in numeric if m.text not in skip]
    if not numeric:
        return [], 0.0
    sizes = sorted({m.size for m in numeric})
    if len(sizes) > 1:
        small = [m for m in numeric if m.size == sizes[0]]
        if small:
            return small, sizes[0]

    clusters = _cluster(numeric)
    out: list[Mark] = []
    for group in clusters:
        if len(group) < 2:
            continue
        least = min(float(m.text) for m in group)
        out.extend(m for m in group if float(m.text) == least)
    return out, sizes[0]


def _cluster(marks: list[Mark], radius: float = 25) -> list[list[Mark]]:
    """Подписи, стоящие вплотную друг к другу, — это одна размерная связка."""
    parent = list(range(len(marks)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(marks):
        for j in range(i + 1, len(marks)):
            b = marks[j]
            if math.hypot(a.x - b.x, a.y - b.y) <= radius:
                parent[find(i)] = find(j)

    groups: dict[int, list[Mark]] = defaultdict(list)
    for i, m in enumerate(marks):
        groups[find(i)].append(m)
    return list(groups.values())


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
    marks = _drop_legend(_marks(page))

    spool_marks = [m for m in marks if SPOOL_RE.match(m.text.replace(" ", ""))]
    if not spool_marks:
        sheet.notes.append(
            "на чертеже нет подписей спулов (SP01, SP02…) — разбивку взять неоткуда")
        return sheet, []

    # Сначала грубая рамка — по одним подписям спулов: они всегда внутри поля
    # чертежа. Размеры подписаны вдоль трубы и вертикальными бывают редко,
    # а штамп и рамка листа набраны вертикально — так и отсеиваются.
    rough = _inside(_drawing_box(spool_marks, margin=0.4))
    numeric = [m for m in marks
               if re.fullmatch(r"\d{2,5}", m.text) and rough(m) and not m.vertical]
    # Диаметры подписаны такими же числами, что и размеры: «50» у DN50 — это
    # не длина реза. Берём диаметры из спецификации листа и исключаем их.
    diameters = {part.strip() for item in sheet.materials
                 for part in re.split(r"[xX]", item.size) if part.strip()}
    lengths, cut_size = _cut_lengths(numeric, diameters)
    if not lengths:
        sheet.notes.append(
            "не удалось отличить длины отрезков трубы от размерных цепочек")
        return sheet, []

    # Теперь рамка точная — по спулам и найденным длинам. Выноска позиции
    # повёрнута как придётся — вдоль трубы или вертикально, — поэтому
    # опознаём её по значению: это номер строки спецификации, не размер.
    inside = _inside(_drawing_box(spool_marks + lengths))
    last = max((int(m.pt_no) for m in sheet.materials if m.pt_no.isdigit()),
               default=0)
    chosen = {id(m) for m in lengths}
    balloons = [m for m in marks
                if re.fullmatch(r"\d{1,3}", m.text) and inside(m)
                and id(m) not in chosen
                and 0 < int(m.text) <= last
                and m.text not in diameters]

    sheet.marked_spools = sorted({_nearest(m, spool_marks) for m in spool_marks})
    return sheet, _rows(sheet, spool_marks, lengths, balloons)


def _nearest_pipe(mark: Mark, pipe_balloons: list[Mark],
                  radius: float = 60) -> str | None:
    """Номер позиции трубы по ближайшей выноске; далёкие выноски не в счёт."""
    if not pipe_balloons:
        return None
    best = min(pipe_balloons,
               key=lambda b: math.hypot(b.x - mark.x, b.y - mark.y))
    if math.hypot(best.x - mark.x, best.y - mark.y) > radius:
        return None
    return best.text.lstrip("0")


def _rows(sheet: Sheet, spool_marks: list[Mark], lengths: list[Mark],
          balloons: list[Mark]) -> list[SpoolRow]:
    fabrication = {m.pt_no: m for m in sheet.materials if not m.erection}
    pipes = {no: m for no, m in fabrication.items() if _mm(m.qty) is not None}

    def in_request(no: str) -> bool:
        """Мелочь ниже DN50 в заявку не идёт; бобышка на основной трубе идёт —
        основной диаметр у неё как раз трубный («80x20»)."""
        dn = _main_dn(fabrication[no].size)
        return dn is None or dn >= 50

    # Длина реза относится к ближайшему спулу, а какая это труба — подсказывает
    # ближайшая выноска с номером позиции: на одной линии их бывает несколько,
    # разного материала и диаметра.
    pipe_balloons = [m for m in balloons if m.text.lstrip("0") in pipes]
    exact = {round(_mm(m.qty)): no for no, m in pipes.items()}
    main = max(pipes, key=lambda no: _mm(pipes[no].qty)) if pipes else None

    totals: dict[tuple[str, str], float] = defaultdict(float)
    for mark in lengths:
        value = float(mark.text)
        no = _nearest_pipe(mark, pipe_balloons) or exact.get(round(value), main)
        if no is not None:
            totals[(_nearest(mark, spool_marks), no)] += value

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
