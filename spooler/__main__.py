"""Точка входа.

Без аргументов открывается окно программы. Если файлы перетащили на значок
run.bat, они приходят сюда аргументами — тогда работаем без окна, как раньше.
"""
import sys


def main() -> int:
    if len(sys.argv) > 1:
        from .cli import main as cli_main
        return cli_main()
    try:
        from .gui import main as gui_main
    except ImportError:
        print("Не удалось открыть окно: в этой сборке Python нет tkinter.\n"
              "На Windows он ставится вместе с Python — переустановите его "
              "с python.org.\n"
              "Пока можно работать без окна: перетащите PDF на run.bat.")
        return 1
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
