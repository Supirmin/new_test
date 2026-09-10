"""Сборка заявки: общий ход работы для окна и для командной строки."""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .excel_writer import Issue, write_workbook
from .pdf_reader import read_pdf
from .reference import Reference
from .spools import process

REFERENCE_FILE = pathlib.Path(__file__).resolve().parent.parent / "spravochnik.json"
DEFAULT_OUT = "Заявка.xlsx"

Log = Callable[[str], None]


@dataclass
class Result:
    out: pathlib.Path
    warning: str = ""
    isos: int = 0
    spools: int = 0
    rows: int = 0
    issues: int = 0
    files: list[str] = field(default_factory=list)


def reference_status(path: pathlib.Path = REFERENCE_FILE) -> str:
    """Короткая строка о справочнике для окна программы."""
    if not path.exists():
        return "не загружен — нужна рабочая книга с листом «База»"
    try:
        return f"загружен, {len(Reference.load(str(path)).entries)} позиций"
    except Exception as exc:                      # файл повреждён или чужого формата
        return f"не прочитан ({exc})"


def save_reference(book: str, path: pathlib.Path = REFERENCE_FILE) -> int:
    """Забрать справочник «База» из рабочей книги и сохранить рядом с программой."""
    reference = Reference.from_workbook(book)
    reference.save(str(path))
    return len(reference.entries)


def collect_pdfs(paths: Iterable[str]) -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for item in paths:
        p = pathlib.Path(item)
        if p.is_dir():
            found.extend(sorted(p.rglob("*.pdf")))
        elif p.suffix.lower() == ".pdf":
            found.append(p)
    seen: set[pathlib.Path] = set()
    unique = []
    for p in found:
        key = p.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def collect_issues(built) -> list[Issue]:
    """Места, где программе нельзя доверять на слово."""
    issues: list[Issue] = []
    for sheet, spools in built:
        where = sheet.iso or f"стр.{sheet.page}"
        for note in sheet.notes:
            issues.append(Issue(where, "", "лист прочитан не полностью", note))
        if sheet.extra_welds:
            issues.append(Issue(
                where, "",
                "на чертеже есть цеховые швы вне ведомости",
                ", ".join(sheet.extra_welds)
                + " — в WELDING LIST их нет, программа их не учла, "
                  "спулов на листе может быть больше",
            ))
        marked = len(sheet.marked_spools)
        if marked and marked != len(spools):
            issues.append(Issue(
                where, "",
                "расходится число спулов",
                f"по ведомости швов получилось {len(spools)}, "
                f"на чертеже размечено {marked}: {', '.join(sheet.marked_spools)}",
            ))
        for weld in sheet.welds:
            if weld.parts is None:
                issues.append(Issue(where, "", "не разобран стык",
                                    f"{weld.no}: LOCATION = {weld.location!r}"))
    return issues


def _why_empty(built) -> str:
    """Своими словами, почему заявка пустая: причина лежит в замечаниях к листам."""
    notes = [n for sheet, _ in built for n in sheet.notes]
    if notes:
        return notes[0]
    if not built:
        return ("ни один лист не удалось прочитать — скорее всего это формат "
                "чертежа, которого программа ещё не знает.")
    return ("листы прочитаны, но состав спулов собрать не из чего. "
            "Подробности на листе «Расхождения».")


def run(paths: Iterable[str], out: str | None = None, log: Log = print,
        reference_path: pathlib.Path = REFERENCE_FILE) -> Result:
    """Прочитать чертежи и записать заявку. Бросает ValueError с внятным текстом."""
    files = collect_pdfs(paths)
    if not files:
        raise ValueError("Не выбрано ни одного PDF с изометриями.")
    if not reference_path.exists():
        raise ValueError(
            "Не загружен справочник «База».\n"
            "Нажмите «Загрузить справочник» и укажите рабочую книгу .xlsx — "
            "это нужно один раз.")

    reference = Reference.load(str(reference_path))
    log(f"Справочник «База»: {len(reference.entries)} позиций.")

    rows, built = [], []
    for i, path in enumerate(files, 1):
        log(f"[{i}/{len(files)}] {path.name}")
        sheets = read_pdf(str(path))
        file_rows, file_built = process(sheets)
        rows.extend(file_rows)
        built.extend(file_built)
        isos = {s.iso for s, _ in file_built if s.iso}
        log(f"        листов {len(file_built)}, изометрий {len(isos)}, "
            f"спулов {sum(len(sp) for _, sp in file_built)}")

    target = pathlib.Path(out) if out else files[0].parent / DEFAULT_OUT
    issues = collect_issues(built)

    # Пустой результат — не повод бросать пользователя без ответа: причина
    # уже собрана в «Расхождениях», её и надо показать.
    warning = ""
    if not rows:
        warning = _why_empty(built)
        log("")
        log("Ни одной строки заявки не собралось. " + warning)
        if not issues:
            issues.append(Issue(
                files[0].name, "", "лист не разобран",
                "программа не нашла на листе ни ведомости швов, ни разметки "
                "спулирования — пришлите этот PDF разработчику"))
    write_workbook(str(target), rows, reference, issues)

    result = Result(out=target, warning=warning, rows=len(rows), issues=len(issues),
                    isos=len({s.iso for s, _ in built if s.iso}),
                    spools=sum(len(sp) for _, sp in built),
                    files=[p.name for p in files])
    log("")
    log(f"Изометрий обработано : {result.isos}")
    log(f"Спулов найдено       : {result.spools}")
    log(f"Строк заявки         : {result.rows}")
    log(f"Строк в расхождениях : {result.issues}")
    log(f"Записано             : {target}")
    return result
