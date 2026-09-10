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


def build_spools(sheet: Sheet, start_number: int = 1) -> list[Spool]:
    fabrication = {m.pt_no for m in sheet.materials if not m.erection}
    if not fabrication:
        return []

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
        if w.is_shop and a in fabrication and b in fabrication:
            uf.union(a, b)
    for w in sheet.welds:
        pair = w.parts
        if pair and w.is_shop and pair[0] in fabrication:
            shop_welds[uf.find(pair[0])].append(w.no)

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
        if a in fabrication and b in fabrication:
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

    spools = []
    for i, root in enumerate(order):
        welds = sorted(set(shop_welds.get(root, [])), key=_weld_no)
        welded = bool(welds)
        number = start_number + i
        spools.append(Spool(
            name=f"{'' if welded else 'E-'}SP{number:02d}",
            parts=sorted(groups[root], key=_part_key),
            welds=welds,
            welded=welded,
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
        counters[key] += len(spools)
        rows.extend(spool_rows(sheet, spools))
        built.append((sheet, spools))
    return rows, built
