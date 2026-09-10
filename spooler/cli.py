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

DEFAULT_REFERENCE = str(pathlib.Path(__file__).resolve().parent.parent / "spravochnik.json")
DEFAULT_OUT = "Заявка.xlsx"


def _own_folder() -> list[str]:
    """Файлы рядом с программой: чтобы запуск двойным щелчком тоже работал."""
    home = pathlib.Path(__file__).resolve().parent.parent
    found = [str(p) for p in sorted(home.glob("*.pdf"))]
    books = [p for p in sorted(home.glob("*.xls*")) if not p.name.startswith("~$")]
    if found:
        print(f"файлы взяты из папки программы: {home}")
        found += [str(p) for p in books]
    return found


def sort_inputs(paths: list[str]) -> tuple[list[pathlib.Path], str | None]:
    """Разложить перетащенные файлы: PDF — чертежи, XLSX — рабочая книга.

    Так значок программы работает как приёмник: бросил на него файлы —
    и не надо помнить, каким ключом что передаётся.
    """
    pdfs: list[pathlib.Path] = []
    book: str | None = None
    for item in paths:
        p = pathlib.Path(item)
        if p.is_dir():
            pdfs.extend(sorted(p.rglob("*.pdf")))
        elif p.suffix.lower() == ".pdf":
            pdfs.append(p)
        elif p.suffix.lower() in (".xlsx", ".xlsm"):
            book = str(p)
        elif not p.exists():
            raise FileNotFoundError(f"не найдено: {item}")
    return pdfs, book


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
    ap.add_argument("pdf", nargs="*",
                    help="PDF с изометриями, папка с ними или рабочая книга .xlsx; "
                         "без аргументов берутся файлы из папки программы")
    ap.add_argument("-b", "--book",
                    help="рабочая книга .xlsx — нужна один раз, чтобы забрать "
                         "справочник «База»")
    ap.add_argument("-r", "--reference", default=DEFAULT_REFERENCE,
                    help="файл сохранённого справочника")
    ap.add_argument("-o", "--out", default=DEFAULT_OUT,
                    help="файл результата (по умолчанию рядом с чертежами)")
    args = ap.parse_args(argv)

    files, dropped_book = sort_inputs(args.pdf or _own_folder())
    reference = load_reference(args.book or dropped_book, args.reference)
    if not files:
        from .hint import TEXT
        raise SystemExit(TEXT)

    if args.out == DEFAULT_OUT:
        # Результат кладём рядом с чертежами, а не в папку программы.
        args.out = str(files[0].parent / DEFAULT_OUT)

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
