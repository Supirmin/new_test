"""Чтение изометрий из PDF.

Текст на листах nanoCAD-экспорта лежит не в шрифтах, а в аннотациях типа Square:
/Contents хранит саму надпись, прямоугольник — её место на листе. Поэтому
get_text() возвращает пустоту, а весь чертёж читается через annots().
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

import pymupdf

WELD_RE = re.compile(r"[SF]\d{1,3}[A-Z]?$")
SPOOL_RE = re.compile(r"\s*(E-)?SP\s*0*(\d{1,3})\s*$")
KATUSHKA_RE = re.compile(r"<\s*(\d{1,3})\s*>$")


def _fix(text: str) -> str:
    """Аннотации записаны в UTF-8, а отдаются как cp1251: -45в„ѓ -> -45℃."""
    try:
        return text.encode("cp1251").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


@dataclass
class Cell:
    x0: float
    y0: float
    x1: float
    text: str


@dataclass
class MaterialItem:
    pt_no: str
    size: str
    ident: str
    description: str
    qty: str
    erection: bool = False

    @property
    def qty_mm(self) -> float | None:
        """Длина трубы в мм, если QTY задан как «745 MM»."""
        m = re.match(r"([\d.,]+)\s*MM$", self.qty.strip(), re.I)
        return float(m.group(1).replace(",", ".")) if m else None

    @property
    def qty_pcs(self) -> float | None:
        """Количество штук, если QTY — просто число."""
        s = self.qty.strip()
        return float(s.replace(",", ".")) if re.fullmatch(r"[\d.,]+", s) else None


@dataclass
class Weld:
    no: str
    size: str
    kind: str          # BW / SW / LET
    shop_field: str    # S — цеховой, F — монтажный
    location: str      # «1-11» — какие PT.NO соединяет

    @property
    def parts(self) -> tuple[str, str] | None:
        m = re.match(r"([\d*]+)\s*-\s*([\d*]+)", self.location.replace(" ", ""))
        return (m.group(1), m.group(2)) if m else None

    @property
    def is_shop(self) -> bool:
        return self.shop_field.strip().upper() == "S"


@dataclass
class Sheet:
    page: int
    iso: str = ""
    line: str = ""
    revision: str = ""
    sheet_no: str = ""
    area: str = ""
    fmt: str = "intergraph"
    prebuilt_rows: list = field(default_factory=list)
    welds: list[Weld] = field(default_factory=list)
    materials: list[MaterialItem] = field(default_factory=list)
    marked_spools: list[str] = field(default_factory=list)
    marked_katushki: list[str] = field(default_factory=list)
    extra_welds: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        """Титул — второй сегмент номера изометрии (NKNH21002-1401-... -> 1401)."""
        parts = self.iso.split("-")
        return parts[1] if len(parts) > 1 else ""

    def material(self, pt_no: str) -> MaterialItem | None:
        for m in self.materials:
            if m.pt_no == pt_no:
                return m
        return None


def _cells(page) -> list[Cell]:
    out = []
    for a in page.annots() or []:
        text = _fix(a.info.get("content", "")).strip()
        if text:
            r = a.rect
            out.append(Cell(r.x0, r.y0, r.x1, text))
    return out


def _rows(cells: list[Cell], tol: float = 3.5) -> list[list[Cell]]:
    """Группировка ячеек в строки по близости y."""
    buckets: dict[int, list[Cell]] = defaultdict(list)
    for c in cells:
        buckets[round(c.y0 / tol)].append(c)
    return [sorted(v, key=lambda c: c.x0) for _, v in sorted(buckets.items())]


def _find(cells: list[Cell], text: str) -> Cell | None:
    for c in cells:
        if c.text == text:
            return c
    return None


def _read_welding_list(cells: list[Cell]) -> list[Weld]:
    head = _find(cells, "WELD NO.")
    if head is None:
        return []
    loc = _find(cells, "LOCATION")
    x_max = (loc.x1 + 40) if loc else head.x0 + 400
    body = [c for c in cells if c.y0 > head.y0 + 3 and head.x0 - 25 < c.x0 < x_max]
    welds = []
    for row in _rows(body):
        vals = [c.text for c in row]
        if len(vals) >= 5 and WELD_RE.match(vals[0]):
            welds.append(Weld(vals[0], vals[1], vals[2], vals[3], vals[4]))
    return welds


def _columns_from_data(body: list[Cell], gap: float = 15.0) -> list[float]:
    """Границы колонок берём из самих данных: x0 ячеек образуют явные кластеры."""
    xs = sorted({round(c.x0, 1) for c in body})
    if not xs:
        return []
    starts = [xs[0]]
    for prev, cur in zip(xs, xs[1:]):
        if cur - prev > gap:
            starts.append(cur)
    return starts


def _read_material_list(cells: list[Cell], header: Cell, y_stop: float) -> list[MaterialItem]:
    """Спецификация MATERIAL LIST: PT.NO | SIZE | ITEM CODE | DESCRIPTION | QTY.

    Колонки определяются кластеризацией x-координат данных, а не позициями
    заголовков: заголовки центрированы над колонками, данные прижаты влево,
    и по одним заголовкам QTY уезжает в DESCRIPTION.
    """
    names = ("PT.NO", "SIZE", "ITEM CODE", "DESCRIPTION", "QTY")
    heads = {n: c for n in names
             for c in cells
             if c.text == n and 0 < c.y0 - header.y0 < 26 and c.x0 > header.x0 - 120}
    if "PT.NO" not in heads:
        return []

    # Правее QTY идёт координатная сетка листа (1, 2, 3...) — она не часть таблицы.
    x_max = heads["QTY"].x1 + 25 if "QTY" in heads else heads["PT.NO"].x0 + 400
    body = [c for c in cells
            if header.y0 + 10 < c.y0 < y_stop
            and heads["PT.NO"].x0 - 12 < c.x0 < x_max]
    starts = _columns_from_data(body)
    if len(starts) < 4:
        return []
    # Первые четыре кластера — PT.NO, SIZE, ITEM CODE, DESCRIPTION; последний — QTY.
    layout = list(zip(starts, ("PT.NO", "SIZE", "ITEM CODE", "DESCRIPTION")))
    layout.append((starts[-1], "QTY"))

    def column_of(cell: Cell) -> str:
        name = layout[0][1]
        for x, n in layout:
            if cell.x0 >= x - 2:
                name = n
        return name

    items: list[MaterialItem] = []
    for row in _rows(body):
        packed: dict[str, list[str]] = defaultdict(list)
        for c in row:
            packed[column_of(c)].append(c.text)
        pt = " ".join(packed.get("PT.NO", []))
        if not re.fullmatch(r"\d{1,3}", pt):
            # строка-продолжение описания: дописываем к предыдущей позиции
            tail = " ".join(packed.get("DESCRIPTION", []))
            if tail and items:
                items[-1].description = f"{items[-1].description} {tail}".strip()
            continue
        items.append(MaterialItem(
            pt_no=pt,
            size=" ".join(packed.get("SIZE", [])),
            ident=" ".join(packed.get("ITEM CODE", [])),
            description=" ".join(packed.get("DESCRIPTION", [])),
            qty=" ".join(packed.get("QTY", [])),
        ))
    return items


def _value_right_of(cells: list[Cell], head: Cell, dy: float = 6) -> str:
    row = [c for c in cells if abs(c.y0 - head.y0) < dy and c.x0 > head.x1]
    return min(row, key=lambda c: c.x0).text if row else ""


def _value_below(cells: list[Cell], head: Cell, dx: float = 40, dy: float = 22,
                 pattern: str | None = None) -> str:
    below = [c for c in cells
             if 0 < c.y0 - head.y0 < dy and abs(c.x0 - head.x0) < dx
             and (pattern is None or re.fullmatch(pattern, c.text))]
    return min(below, key=lambda c: c.y0).text if below else ""


def _read_title_block(cells: list[Cell], sheet: Sheet) -> None:
    """Штамп Intergraph: номер изометрии справа от подписи, остальное — под ней."""
    head = _find(cells, "PROJECT NO.")
    if head is not None:
        value = _value_right_of(cells, head)
        # На части листов поле пустое, и справа оказывается подпись соседней
        # графы штампа — такой «номер» брать нельзя.
        if "ISO" in value.upper():
            sheet.iso = value

    head = _find(cells, "AREA")
    if head is not None:
        sheet.area = _value_right_of(cells, head, dy=12)

    head = _find(cells, "PIPELINE REFERENCE")
    if head is not None:
        sheet.line = _value_below(cells, head, dx=60)

    # Номер листа стоит под «DRG. NO.» и дополняет номер изометрии до ISO-000N.
    head = _find(cells, "DRG. NO.")
    if head is not None:
        no = _value_below(cells, head, dx=20, pattern=r"\d{1,2}")
        if no:
            sheet.sheet_no = no
            if re.search(r"ISO-0*\d*$", sheet.iso):
                sheet.iso = re.sub(r"(ISO-)0*\d*$",
                                   lambda m: m.group(1) + no.zfill(4), sheet.iso)

    # Ревизия — в журнале изменений слева внизу, колонка REVISION.
    head = _find(cells, "REVISION")
    if head is not None:
        above = [c for c in cells
                 if 0 < head.y0 - c.y0 < 20 and abs(c.x0 - head.x0) < 30
                 and re.fullmatch(r"\d{1,2}", c.text)]
        if above:
            sheet.revision = max(above, key=lambda c: c.y0).text
    if not sheet.revision:
        sheet.revision = "0"


def _read_marks(cells: list[Cell], sheet: Sheet) -> None:
    """Ручная разметка спулирования, нанесённая поверх изометрии."""
    legend = [c for c in cells
              if "Номер спула" in c.text or "Условные" in c.text
              or "шов" in c.text or "Границы спулов" in c.text
              or "Номер катушки" in c.text]

    def in_legend(c: Cell) -> bool:
        return any(abs(c.y0 - l.y0) < 30 and abs(c.x0 - l.x0) < 60 for l in legend)

    table = {w.no for w in sheet.welds}
    for c in cells:
        if in_legend(c):
            continue
        m = SPOOL_RE.match(c.text)
        if m:
            sheet.marked_spools.append(f"{'E-' if m.group(1) else ''}SP{int(m.group(2)):02d}")
            continue
        m = KATUSHKA_RE.match(c.text)
        if m:
            sheet.marked_katushki.append(m.group(1))
            continue
        if WELD_RE.match(c.text) and c.text not in table:
            sheet.extra_welds.append(c.text)
    sheet.marked_spools = sorted(set(sheet.marked_spools))
    sheet.marked_katushki = sorted(set(sheet.marked_katushki), key=int)
    sheet.extra_welds = sorted(set(sheet.extra_welds), key=lambda s: (s[0], int(s[1:])))


def read_sheet(page, page_no: int) -> Sheet:
    cells = _cells(page)
    sheet = Sheet(page=page_no)
    if not cells:
        sheet.notes.append("лист без аннотаций — вероятно другой формат изометрии")
        return sheet

    sheet.welds = _read_welding_list(cells)
    _read_title_block(cells, sheet)

    fab = _find(cells, "MATERIAL LIST - FABRICATION")
    ere = _find(cells, "MATERIAL LIST - ERECTION")
    weld_head = _find(cells, "WELDING LIST")
    if fab is not None:
        stop = ere.y0 - 4 if ere is not None else fab.y0 + 400
        sheet.materials += _read_material_list(cells, fab, stop)
    if ere is not None:
        stop = weld_head.y0 - 4 if weld_head is not None else ere.y0 + 400
        items = _read_material_list(cells, ere, stop)
        for it in items:
            it.erection = True
        sheet.materials += items

    _read_marks(cells, sheet)
    if not sheet.welds:
        sheet.notes.append("на листе нет ведомости швов (WELDING LIST)")
    if not sheet.materials:
        sheet.notes.append("на листе не прочитана спецификация (MATERIAL LIST)")
    return sheet


def read_pdf(path: str) -> list[Sheet]:
    from . import sibur                       # отложенный импорт: sibur читает Sheet

    doc = pymupdf.open(path)
    sheets = []
    for i, page in enumerate(doc):
        if sibur.looks_like_sibur(page):
            sheet, rows = sibur.read(page, i + 1)
            sheet.prebuilt_rows = rows
        else:
            sheet = read_sheet(page, i + 1)
        sheets.append(sheet)
    return sheets


def derive_iso(sheets: list[Sheet]) -> None:
    """Достроить номер изометрии там, где поле в штампе пустое.

    Номер собирается по образцу соседних листов той же подшивки:
    NKNH21002-1401-270110206DM-TK10.ISO-0001 = префикс + титул + код линии +
    суффикс + номер листа. Титул берётся из графы AREA, код линии — из
    PIPELINE REFERENCE.
    """
    sample = next((s.iso for s in sheets if "ISO" in s.iso.upper()), "")
    m = re.match(r"(.+?)-(\d+)-([A-Za-z0-9]+)-(.*ISO-)0*\d*$", sample)
    if not m:
        return
    prefix, _, _, suffix = m.groups()

    for sheet in sheets:
        if sheet.iso or not sheet.line:
            continue
        title = sheet.area.split("-")[0] if sheet.area else ""
        parts = sheet.line.split("-")
        if not title or len(parts) < 3:
            continue
        code = f"{parts[0]}{parts[1]}{parts[2]}"
        no = (sheet.sheet_no or "1").zfill(4)
        sheet.iso = f"{prefix}-{title}-{code}-{suffix}{no}"
        sheet.notes.append(
            f"номер изометрии не заполнен в штампе — собран по образцу подшивки: {sheet.iso}")
