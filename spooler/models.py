"""Общие типы для обоих форматов изометрий."""
from __future__ import annotations

from dataclasses import dataclass


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
