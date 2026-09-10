"""Справочник «База» из рабочей книги: Ident -> перевод, наименование, единица.

На том же листе, в колонках G/H, лежит независимая таблица перевода
дюймов в DN — ей пользуются формулы книги, ей же пользуемся и мы.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict

import openpyxl

SHEET = "База"


@dataclass
class Entry:
    ident: str
    kind: str          # Труба / Отвод / Тройник / Фланец / Бобышка / Переход
    name: str
    unit: str


class Reference:
    def __init__(self, entries: dict[str, Entry], dn_to_inch: dict[float, str]) -> None:
        self.entries = entries
        self.dn_to_inch = dn_to_inch

    @classmethod
    def from_workbook(cls, path: str) -> "Reference":
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb[SHEET]
        except KeyError as exc:
            raise KeyError(f"в книге нет листа «{SHEET}»") from exc

        entries: dict[str, Entry] = {}
        dn_to_inch: dict[float, str] = {}
        for row in ws.iter_rows(min_row=2, values_only=True):
            ident = str(row[0]).strip() if row[0] else ""
            if ident and ident not in entries:
                entries[ident] = Entry(
                    ident=ident,
                    kind=str(row[1]).strip() if row[1] else "",
                    name=str(row[2]).strip() if row[2] else "",
                    unit=str(row[3]).strip() if len(row) > 3 and row[3] else "",
                )
            if len(row) > 7 and row[6] is not None and row[7] is not None:
                try:
                    dn_to_inch[float(row[7])] = _fmt_inch(float(row[6]))
                except (TypeError, ValueError):
                    pass
        wb.close()
        return cls(entries, dn_to_inch)

    def lookup(self, ident: str) -> Entry | None:
        return self.entries.get(ident.strip())

    def inches(self, size: str) -> str:
        """«100X80» -> «4x3», «100X20» -> «4x0,75»."""
        parts = re.split(r"[xX]", size.strip())
        out = []
        for p in parts:
            try:
                out.append(self.dn_to_inch.get(float(p), ""))
            except ValueError:
                out.append("")
        return "x".join(out) if all(out) else ""



    def save(self, path: str) -> None:
        """Сохранить справочник рядом с программой, чтобы не таскать книгу."""
        data = {
            "entries": {k: asdict(v) for k, v in self.entries.items()},
            "dn_to_inch": {str(k): v for k, v in self.dn_to_inch.items()},
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)

    @classmethod
    def load(cls, path: str) -> "Reference":
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        entries = {k: Entry(**v) for k, v in data["entries"].items()}
        dn = {float(k): v for k, v in data["dn_to_inch"].items()}
        return cls(entries, dn)

def _fmt_inch(value: float) -> str:
    text = f"{value:g}".replace(".", ",")
    return text


# Запасной перевод по английскому описанию из спецификации: нужен, когда
# в справочнике «База» ещё нет такой позиции — на новых схемах это обычное дело.
_BY_DESCRIPTION: tuple[tuple[str, str, str], ...] = (
    ("blind flange", "Заглушка", "шт"),
    ("figure 8 blank", "Заглушка", "шт"),
    ("spectacle", "Заглушка", "шт"),
    ("reducer", "Переход", "шт"),
    ("tee", "Тройник", "шт"),
    ("elbow", "Отвод", "шт"),
    ("olet", "Бобышка", "шт"),
    ("flange", "Фланец", "шт"),
    ("gasket", "Прокладка", "шт"),
    ("coupling", "Муфта", "шт"),
    ("cap", "Заглушка", "шт"),
    ("pipe", "Труба", "мм"),
)


def guess_entry(ident: str, description: str) -> Entry | None:
    """Определить тип детали по описанию, когда Ident не найден в «Базе»."""
    text = description.lower()
    for keyword, kind, unit in _BY_DESCRIPTION:
        if keyword in text:
            return Entry(ident=ident, kind=kind, name=description, unit=unit)
    return None
