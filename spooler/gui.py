"""Окно программы: выбираете чертежи — получаете заявку."""
from __future__ import annotations

import os
import pathlib
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import job

TITLE = "Заявка по спулам"
PDF_TYPES = [("Изометрии PDF", "*.pdf"), ("Все файлы", "*.*")]
BOOK_TYPES = [("Книга Excel", "*.xlsx *.xlsm"), ("Все файлы", "*.*")]


def open_in_explorer(path: pathlib.Path) -> None:
    """Показать файл в проводнике — на каждой системе по-своему."""
    try:
        if sys.platform == "win32":
            subprocess.run(["explorer", "/select,", str(path)], check=False)
        elif sys.platform == "darwin":
            subprocess.run(["open", "-R", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path.parent)], check=False)
    except OSError as exc:
        messagebox.showwarning(TITLE, f"Не удалось открыть папку:\n{exc}")


def open_file(path: pathlib.Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))                      # noqa: S606 — штатный способ Windows
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except OSError as exc:
        messagebox.showwarning(TITLE, f"Не удалось открыть файл:\n{exc}")


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.files: list[str] = []
        self.result: job.Result | None = None
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()

        root.title(TITLE)
        root.minsize(720, 560)
        self._build()
        self._refresh_reference()
        self._refresh_buttons()
        root.after(100, self._drain)

    # ---------- разметка окна ----------

    def _build(self) -> None:
        pad = dict(padx=12, pady=6)
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)

        step1 = ttk.LabelFrame(root, text=" 1. Чертежи ")
        step1.grid(row=0, column=0, sticky="ew", **pad)
        step1.columnconfigure(0, weight=1)

        buttons = ttk.Frame(step1)
        buttons.grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))
        ttk.Button(buttons, text="Выбрать файлы PDF…",
                   command=self.choose_files).pack(side="left")
        ttk.Button(buttons, text="Выбрать папку…",
                   command=self.choose_folder).pack(side="left", padx=6)
        self.clear_btn = ttk.Button(buttons, text="Очистить", command=self.clear_files)
        self.clear_btn.pack(side="left")

        box = ttk.Frame(step1)
        box.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        box.columnconfigure(0, weight=1)
        self.file_list = tk.Listbox(box, height=5, activestyle="none")
        self.file_list.grid(row=0, column=0, sticky="ew")
        bar = ttk.Scrollbar(box, orient="vertical", command=self.file_list.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.file_list.configure(yscrollcommand=bar.set)

        step2 = ttk.LabelFrame(root, text=" 2. Справочник «База» ")
        step2.grid(row=1, column=0, sticky="ew", **pad)
        step2.columnconfigure(0, weight=1)
        self.ref_label = ttk.Label(step2, text="")
        self.ref_label.grid(row=0, column=0, sticky="w", padx=8, pady=8)
        ttk.Button(step2, text="Загрузить из рабочей книги…",
                   command=self.choose_book).grid(row=0, column=1, padx=8, pady=8)

        self.run_btn = ttk.Button(root, text="Собрать заявку", command=self.start)
        self.run_btn.grid(row=2, column=0, sticky="ew", padx=12, pady=(6, 2))

        self.progress = ttk.Progressbar(root, mode="indeterminate")

        log_frame = ttk.LabelFrame(root, text=" Ход работы ")
        log_frame.grid(row=3, column=0, sticky="nsew", **pad)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = ScrolledText(log_frame, height=12, wrap="word", state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        bottom = ttk.Frame(root)
        bottom.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.open_btn = ttk.Button(bottom, text="Открыть заявку",
                                   command=self.open_result, state="disabled")
        self.open_btn.pack(side="left")
        self.folder_btn = ttk.Button(bottom, text="Показать в папке",
                                     command=self.show_result, state="disabled")
        self.folder_btn.pack(side="left", padx=6)

        self._write("Выберите чертежи и нажмите «Собрать заявку».\n"
                    "Справочник «База» нужно загрузить один раз — "
                    "дальше он берётся сам.\n")

    # ---------- состояние ----------

    def _write(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _refresh_reference(self) -> None:
        self.ref_label.configure(text=job.reference_status())

    def _refresh_buttons(self) -> None:
        ready = bool(self.files) and job.REFERENCE_FILE.exists()
        self.run_btn.configure(state="normal" if ready else "disabled")
        self.clear_btn.configure(state="normal" if self.files else "disabled")

    def _set_files(self, paths: list[str]) -> None:
        self.files = paths
        self.file_list.delete(0, "end")
        for p in paths:
            self.file_list.insert("end", p)
        self._refresh_buttons()

    # ---------- кнопки ----------

    def choose_files(self) -> None:
        chosen = filedialog.askopenfilenames(title="Выберите изометрии",
                                             filetypes=PDF_TYPES)
        if chosen:
            self._set_files(list(chosen))
            self._write(f"Выбрано файлов: {len(chosen)}")

    def choose_folder(self) -> None:
        folder = filedialog.askdirectory(title="Выберите папку с изометриями")
        if not folder:
            return
        found = job.collect_pdfs([folder])
        if not found:
            messagebox.showinfo(TITLE, "В этой папке нет ни одного PDF.")
            return
        self._set_files([str(p) for p in found])
        self._write(f"В папке найдено PDF: {len(found)}")

    def clear_files(self) -> None:
        self._set_files([])

    def choose_book(self) -> None:
        book = filedialog.askopenfilename(
            title="Рабочая книга с листом «База»", filetypes=BOOK_TYPES)
        if not book:
            return
        try:
            count = job.save_reference(book)
        except KeyError:
            messagebox.showerror(TITLE, "В этой книге нет листа «База».")
            return
        except Exception as exc:
            messagebox.showerror(TITLE, f"Не удалось прочитать книгу:\n{exc}")
            return
        self._write(f"Справочник загружен: {count} позиций. "
                    "Больше эта книга не понадобится.")
        self._refresh_reference()
        self._refresh_buttons()

    def start(self) -> None:
        out = filedialog.asksaveasfilename(
            title="Куда сохранить заявку", defaultextension=".xlsx",
            initialfile=job.DEFAULT_OUT,
            initialdir=str(pathlib.Path(self.files[0]).parent),
            filetypes=[("Книга Excel", "*.xlsx")])
        if not out:
            return
        self.run_btn.configure(state="disabled")
        self.open_btn.configure(state="disabled")
        self.folder_btn.configure(state="disabled")
        self.progress.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 4))
        self.progress.start(12)
        self._write("\n" + "─" * 60)
        threading.Thread(target=self._work, args=(list(self.files), out),
                         daemon=True).start()

    # ---------- работа в фоне ----------

    def _work(self, files: list[str], out: str) -> None:
        try:
            result = job.run(files, out=out, log=lambda s: self.messages.put(("log", s)))
            self.messages.put(("done", result))
        except ValueError as exc:
            self.messages.put(("error", str(exc)))
        except Exception as exc:                          # неожиданное — показать целиком
            self.messages.put(("error", f"{type(exc).__name__}: {exc}"))

    def _drain(self) -> None:
        while True:
            try:
                kind, payload = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._write(str(payload))
            elif kind == "done":
                self._finish(payload)
            elif kind == "error":
                self._fail(str(payload))
        self.root.after(100, self._drain)

    def _stop_progress(self) -> None:
        self.progress.stop()
        self.progress.grid_remove()
        self.run_btn.configure(state="normal")

    def _finish(self, result: job.Result) -> None:
        self._stop_progress()
        self.result = result
        self.open_btn.configure(state="normal")
        self.folder_btn.configure(state="normal")
        tail = (f"\nГотово. {result.rows} строк заявки по {result.spools} спулам."
                + (f"\nПроверьте лист «Расхождения» — там {result.issues} "
                   f"строк." if result.issues else ""))
        self._write(tail)
        messagebox.showinfo(TITLE, f"Заявка собрана:\n{result.out}" + tail)

    def _fail(self, text: str) -> None:
        self._stop_progress()
        self._write("Ошибка: " + text)
        messagebox.showerror(TITLE, text)

    # ---------- результат ----------

    def open_result(self) -> None:
        if self.result:
            open_file(self.result.out)

    def show_result(self) -> None:
        if self.result:
            open_in_explorer(self.result.out)


def main() -> int:
    try:                                                  # чёткий текст на экранах с масштабом
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)    # type: ignore[attr-defined]
    except Exception:
        pass
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
