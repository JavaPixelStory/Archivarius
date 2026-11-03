"""Точка входа «Архивариуса».

Запуск из исходников:  python main.py
Запуск с папкой:       python main.py "C:\\Photos"
"""
from __future__ import annotations

import sys


def main() -> int:
    if sys.version_info < (3, 11):
        sys.stderr.write("Нужен Python 3.11 или новее.\n")
        return 1
    try:
        import tkinter  # noqa: F401
    except ImportError:
        sys.stderr.write(
            "В этой сборке Python нет tkinter.\n"
            "Переустановите Python с официального сайта, отметив «tcl/tk and IDLE».\n"
        )
        return 1

    import ui

    ui.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
