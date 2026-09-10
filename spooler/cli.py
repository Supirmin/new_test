"""Командная строка: новые изометрии в PDF -> заявка в Excel.

Эталонная заявка программе не нужна: она читает сами чертежи. Из рабочей
книги берётся только справочник «База» (Ident -> перевод, наименование,
единица измерения), и его достаточно выгрузить один раз.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

from .excel_writer import Issue, write_workbook
from .pdf_reader import read_pdf
from .reference import Reference
from .spools import process

DEFAULT_REFERENCE = "spravochnik.json"


def collect_pdfs(paths: list[str]) -> list[pathlib.Path]:
    """Аргументом можно давать и файлы, и папки с изометриями."""
    found: list[pathlib.Path] = []
    for item in paths:
        p = pathlib.Path(item)
        if p.is_dir():
            found.extend(sorted(p.rglob("*.pdf")))
        elif p.is_file():
            found.append(p)
        else:
            raise FileNotFoundError(f"не найдено: {item}")
    return found


def load_reference(book: str | None, cache: str) -> Reference:
    cache_path = pathlib.Path(cache)
    if book:
        reference = Reference.from_workbook(book)
        reference.save(cache)
        print(f"справочник «База»: {len(reference.entries)} позиций из книги, "
              f"сохранён в {cache}")
        return reference
    if cache_path.exists():
        reference = Reference.load(cache)
        print(f"справочник «База»: {len(reference.entries)} позиций из {cache}")
        return reference
    raise SystemExit(
        f"нет справочника. Первый раз запустите с --book «рабочая книга.xlsx» — "
        f"справочник «База» сохранится в {cache} и дальше книга не понадобится.")


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="spooler",
        description="Читает изометрии из PDF и собирает заявку по спулам в Excel.")
    ap.add_argument("pdf", nargs="+", help="PDF с изометриями или папка с ними")
    ap.add_argument("-b", "--book",
                    help="рабочая книга .xlsx — нужна один раз, чтобы забрать "
                         "справочник «База»")
    ap.add_argument("-r", "--reference", default=DEFAULT_REFERENCE,
                    help=f"файл сохранённого справочника (по умолчанию {DEFAULT_REFERENCE})")
    ap.add_argument("-o", "--out", default="Заявка.xlsx", help="файл результата")
    args = ap.parse_args(argv)

    reference = load_reference(args.book, args.reference)
    files = collect_pdfs(args.pdf)
    if not files:
        raise SystemExit("не найдено ни одного PDF")

    rows, built = [], []
    for path in files:
        sheets = read_pdf(str(path))
        file_rows, file_built = process(sheets)
        rows.extend(file_rows)
        built.extend(file_built)
        isos = {s.iso for s, _ in file_built if s.iso}
        print(f"  {path.name}: листов {len(file_built)}, изометрий {len(isos)}, "
              f"спулов {sum(len(sp) for _, sp in file_built)}")

    issues = collect_issues(built)
    write_workbook(args.out, rows, reference, issues)

    print()
    print(f"изометрий обработано : {len({s.iso for s, _ in built if s.iso})}")
    print(f"спулов найдено       : {sum(len(sp) for _, sp in built)}")
    print(f"строк заявки         : {len(rows)}")
    print(f"строк в расхождениях : {len(issues)}")
    print(f"записано             : {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
