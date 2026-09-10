"""Командная строка — то же самое, что окно программы, но без окна."""
from __future__ import annotations

import argparse
import pathlib
import sys

from . import job


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="spooler",
        description="Читает изометрии из PDF и собирает заявку по спулам в Excel.")
    ap.add_argument("pdf", nargs="*",
                    help="PDF с изометриями или папка с ними; "
                         "без аргументов берутся файлы из папки программы")
    ap.add_argument("-b", "--book",
                    help="рабочая книга .xlsx — нужна один раз, чтобы забрать "
                         "справочник «База»")
    ap.add_argument("-o", "--out", help="файл результата "
                                        "(по умолчанию рядом с чертежами)")
    ap.add_argument("--gui", action="store_true", help="открыть окно программы")
    args = ap.parse_args(argv)

    if args.gui:
        from .gui import main as gui_main
        return gui_main()

    if args.book:
        count = job.save_reference(args.book)
        print(f"Справочник «База»: {count} позиций, сохранён в {job.REFERENCE_FILE}")

    paths = args.pdf or [str(job.REFERENCE_FILE.parent)]
    try:
        job.run(paths, out=args.out)
    except ValueError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
