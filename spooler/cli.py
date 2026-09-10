"""Командная строка: PDF с изометриями -> заявка в Excel."""
from __future__ import annotations

import argparse
import sys

from .excel_writer import Issue, write_workbook
from .pdf_reader import read_pdf
from .reference import Reference
from .spools import process


def collect_issues(built) -> list[Issue]:
    """Места, где программе нельзя доверять на слово."""
    issues: list[Issue] = []
    for sheet, spools in built:
        for note in sheet.notes:
            issues.append(Issue(sheet.iso or f"стр.{sheet.page}", "", "лист прочитан не полностью", note))
        if sheet.extra_welds:
            issues.append(Issue(
                sheet.iso, "",
                "на чертеже есть швы вне ведомости",
                "добавлены вручную при спулировании: " + ", ".join(sheet.extra_welds)
                + " — программа их не учла, спулов может быть больше",
            ))
        marked = len(sheet.marked_spools)
        if marked and marked != len(spools):
            issues.append(Issue(
                sheet.iso, "",
                "расходится число спулов",
                f"по ведомости швов получилось {len(spools)}, "
                f"на чертеже размечено {marked}: {', '.join(sheet.marked_spools)}",
            ))
        for weld in sheet.welds:
            if weld.parts is None:
                issues.append(Issue(sheet.iso, "", "не разобран стык",
                                    f"{weld.no}: LOCATION = {weld.location!r}"))
            elif "*" in weld.location:
                issues.append(Issue(sheet.iso, "", "стык уходит за пределы листа",
                                    f"{weld.no}: LOCATION = {weld.location!r}"))
    return issues


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="spooler",
        description="Разбор изометрий из PDF и сборка заявки по спулам в Excel.")
    ap.add_argument("pdf", help="PDF с изометриями")
    ap.add_argument("-b", "--book", required=True,
                    help="рабочая книга .xlsx — из неё берётся справочник «База»")
    ap.add_argument("-o", "--out", default="Заявка.xlsx", help="файл результата")
    args = ap.parse_args(argv)

    sheets = read_pdf(args.pdf)
    rows, built = process(sheets)
    reference = Reference.from_workbook(args.book)
    issues = collect_issues(built)
    write_workbook(args.out, rows, reference, issues)

    isos = {s.iso for s, _ in built if s.iso}
    spools = sum(len(sp) for _, sp in built)
    print(f"листов прочитано : {len(built)}")
    print(f"изометрий        : {len(isos)}")
    print(f"спулов найдено   : {spools}")
    print(f"строк заявки     : {len(rows)}")
    print(f"расхождений      : {len(issues)}")
    print(f"записано         : {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
