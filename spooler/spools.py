"""Разбивка изометрии на спулы.

Правило, подтверждённое сверкой с эталоном и журналом сварки:

* детали, связанные между собой **цеховыми** (S) швами, образуют один спул — SP;
* деталь спецификации FABRICATION, которой не касается ни один цеховой шов,
  становится отдельным «спулом без сварки» — E-SP (по легенде чертежа);
* монтажные (F) швы спулы не образуют — они соединяют спулы между собой;
* позиции из спецификации ERECTION в спулы не входят.

Нумерация сквозная по линии и идёт по ходу трубы: спулы сцепляются монтажными
швами в цепочку, обход начинается с детали с наименьшим номером.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .pdf_reader import MaterialItem, Sheet, derive_iso


@dataclass
class Spool:
    name: str = ""
    parts: list[str] = field(default_factory=list)
    welds: list[str] = field(default_factory=list)
    welded: bool = True          # False -> E-SP, спул без сварки
    continues: bool = False      # продолжается на следующем листе

    @property
    def kind(self) -> str:
        return "SP" if self.welded else "E-SP"


@dataclass
class SpoolRow:
    """Строка заявки: один материал в одном спуле."""
    iso: str
    line: str
    revision: str
    spool: str
    ident: str
    qty: float
    unit: str                    # «мм» для трубы, «шт» для фасонных изделий
    size: str
    description: str


def _weld_no(weld: str) -> int:
    m = re.search(r"\d+", weld)
    return int(m.group()) if m else 0


class _Union:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, a: str) -> str:
        self.parent.setdefault(a, a)
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _part_key(pt: str) -> int:
    return int(pt) if pt.isdigit() else 10**6


def _capacity(item: MaterialItem | None) -> int:
    """Сколько стыковых швов физически может иметь деталь."""
    text = (item.description if item else "").lower()
    if text.startswith("tee") or " tee" in text:
        return 3
    return 2


def cross_sheet_welds(sheet: Sheet) -> set[str]:
    """Швы, ссылающиеся на деталь соседнего листа.

    Нумерация деталей у Intergraph своя на каждом листе, поэтому шов на стыке
    листов ссылается на номер с продолжения. Отличить его можно по перебору
    концов: у трубы или отвода их два, у тройника три, а приварка бобышки
    (LET) конец не занимает. Всё сверх нормы — ссылка за пределы листа.
    """
    ends: dict[str, list[str]] = defaultdict(list)
    for w in sheet.welds:
        pair = w.parts
        if not pair or w.kind.strip().upper() == "LET":
            continue
        for pt in pair:
            if pt != "*":
                ends[pt].append(w.no)

    crossing: set[str] = set()
    for pt, welds in ends.items():
        limit = _capacity(sheet.material(pt))
        if len(welds) > limit:
            for weld in sorted(welds, key=_weld_no)[limit:]:
                crossing.add(weld)
    return crossing


def build_spools(sheet: Sheet, start_number: int = 1) -> list[Spool]:
    fabrication = {m.pt_no for m in sheet.materials if not m.erection}
    if not fabrication:
        return []
    crossing = cross_sheet_welds(sheet)

    # 1. Склейка деталей цеховыми швами.
    uf = _Union()
    for pt in fabrication:
        uf.find(pt)
    shop_welds: dict[str, list[str]] = defaultdict(list)
    for w in sheet.welds:
        pair = w.parts
        if not pair:
            continue
        a, b = pair
        if w.is_shop and a in fabrication and b in fabrication and w.no not in crossing:
            uf.union(a, b)
    for w in sheet.welds:
        pair = w.parts
        if not pair or not w.is_shop:
            continue
        # Шов на границе листа тоже делает спул сварным, хотя вторая деталь
        # лежит на соседнем листе.
        for pt in pair:
            if pt in fabrication:
                shop_welds[uf.find(pt)].append(w.no)
                break

    groups: dict[str, set[str]] = defaultdict(set)
    for pt in fabrication:
        groups[uf.find(pt)].add(pt)

    # 2. Цепочка спулов: монтажные швы связывают соседние спулы.
    links: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for w in sheet.welds:
        pair = w.parts
        if not pair or w.is_shop:
            continue
        a, b = pair
        if a in fabrication and b in fabrication and w.no not in crossing:
            ra, rb = uf.find(a), uf.find(b)
            if ra != rb:
                links[ra].append((_weld_no(w.no), rb))
                links[rb].append((_weld_no(w.no), ra))

    # 3. Обход по ходу трубы: от детали с наименьшим номером, на развилке —
    #    шов с меньшим номером.
    order: list[str] = []
    seen: set[str] = set()
    roots = sorted(groups, key=lambda r: min(_part_key(p) for p in groups[r]))
    for root in roots:
        if root in seen:
            continue
        stack = [root]
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            order.append(node)
            nxt = [r for _, r in sorted(links.get(node, [])) if r not in seen]
            stack.extend(reversed(nxt))

    # Спул продолжается на соседнем листе, если у детали остался свободный
    # конец: у трубы и отвода их два, у тройника три, приварка бобышки (LET)
    # конец не занимает. Недостача концов означает, что труба уходит за лист.
    ends: dict[str, int] = defaultdict(int)
    for w in sheet.welds:
        pair = w.parts
        if not pair or w.kind.strip().upper() == "LET":
            continue
        for pt in pair:
            if pt in fabrication:
                ends[pt] += 1

    leaving: set[str] = set()
    for pt in fabrication:
        if ends[pt] < _capacity(sheet.material(pt)):
            leaving.add(uf.find(pt))
    for w in sheet.welds:
        pair = w.parts
        if pair and w.is_shop and (w.no in crossing or "*" in w.location):
            for pt in pair:
                if pt in fabrication:
                    leaving.add(uf.find(pt))

    spools = []
    for i, root in enumerate(order):
        welds = sorted(set(shop_welds.get(root, [])), key=_weld_no)
        # Спул, уходящий за лист, сварной: цеховой шов у него на соседнем листе.
        welded = bool(welds) or root in leaving
        number = start_number + i
        spools.append(Spool(
            name=f"{'' if welded else 'E-'}SP{number:02d}",
            parts=sorted(groups[root], key=_part_key),
            welds=welds,
            welded=welded,
            continues=root in leaving,
        ))
    return spools


def _unit_of(item: MaterialItem) -> str:
    """Труба считается в миллиметрах, фасонные изделия — в штуках."""
    return "мм" if item.qty_mm is not None else "шт"


def spool_rows(sheet: Sheet, spools: list[Spool]) -> list[SpoolRow]:
    """Позиции заявки: количество суммируется по (спул, Ident)."""
    rows: list[SpoolRow] = []
    for spool in spools:
        totals: dict[str, list] = {}
        for pt in spool.parts:
            item = sheet.material(pt)
            if item is None:
                continue
            qty = item.qty_mm if item.qty_mm is not None else item.qty_pcs
            if qty is None:
                continue
            key = item.ident
            if key in totals:
                totals[key][0] += qty
            else:
                totals[key] = [qty, item]
        for ident, (qty, item) in totals.items():
            rows.append(SpoolRow(
                iso=sheet.iso,
                line=sheet.line,
                revision=sheet.revision,
                spool=spool.name,
                ident=ident,
                qty=qty,
                unit=_unit_of(item),
                size=item.size,
                description=item.description,
            ))
    return rows


def _marked_start(sheet: Sheet, count: int) -> int | None:
    numbers = sorted({int(re.sub(r"\D", "", n)) for n in sheet.marked_spools
                      if re.sub(r"\D", "", n)})
    if len(numbers) != count or not numbers:
        return None
    return numbers[0]


def process(sheets: list[Sheet]) -> tuple[list[SpoolRow], list[tuple[Sheet, list[Spool]]]]:
    """Нумерация сквозная в пределах одной линии, листы идут по порядку."""
    derive_iso(sheets)
    counters: dict[str, int] = defaultdict(lambda: 1)
    rows: list[SpoolRow] = []
    built: list[tuple[Sheet, list[Spool]]] = []
    for sheet in sheets:
        if not sheet.welds and not sheet.materials:
            continue
        key = sheet.line or sheet.iso
        spools = build_spools(sheet, counters[key])
        # Если на чертеже размечено столько же спулов, сколько насчитала
        # программа, доверяем нумерации чертежа — она учитывает переходы
        # спулов с листа на лист.
        anchor = _marked_start(sheet, len(spools))
        if anchor is not None and anchor != counters[key]:
            counters[key] = anchor
            spools = build_spools(sheet, anchor)
        # Если последний спул листа уходит на следующий лист, там он
        # продолжается под тем же номером.
        step = len(spools)
        if spools and spools[-1].continues:
            step -= 1
        counters[key] += max(step, 0)
        rows.extend(spool_rows(sheet, spools))
        built.append((sheet, spools))
    return rows, built
