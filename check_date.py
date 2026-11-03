"""Диагностика дат: показывает, какие даты видит программа у файла и что выбирает.

Запуск:
    python check_date.py "D:\\Test\\1392373.jpg"
    python check_date.py "D:\\Test"            # по всем файлам в папке

Ничего не переименовывает и не меняет — только читает.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import core
import settings as cfgmod
import version


def describe(path: Path, cfg: dict) -> None:
    try:
        st = os.stat(core.wp(path), follow_symlinks=False)
    except OSError as exc:
        print(f"\n{path.name}")
        print(f"  не прочитать: {core.human_error(exc)}")
        return

    print(f"\n{path.name}")
    print(f"  размер: {st.st_size} байт")
    try:
        print(f"  файловая система — изменён: {datetime.fromtimestamp(st.st_mtime)}")
        print(f"  файловая система — создан : {datetime.fromtimestamp(st.st_ctime)}")
    except (OSError, OverflowError, ValueError):
        print("  файловые времена прочитать не удалось")

    candidates = core.collect_dates(path, st, cfg)
    if not candidates:
        print("  кандидатов нет — файл попадёт в «дата недостоверна»")
        return

    chosen = core.detect_date(path, st, cfg)
    for value, label in sorted(candidates, key=lambda item: item[0]):
        mark = "  <-- ВЫБРАНО" if chosen[0] == value and chosen[1] == label else ""
        print(f"  {value}  ({label}){mark}")

    if chosen[0] is not None:
        name = core.render_name(
            cfg.get("template", "{YYYY}-{MM}-{DD}_{HH}-{mm}-{ss}"),
            chosen[0],
            path.stem,
            path.suffix.lstrip("."),
            "",
        )
        folder = core._render(cfg.get("year_folder", "{YYYY}"), chosen[0], "", "", "")
        print(f"  ИТОГ: папка «{folder}», имя «{name}»")


def main(argv: list[str]) -> int:
    print(f"Архивариус {version.APP_VERSION}")
    print(f"Файл программы: {Path(__file__).resolve()}")
    print(f"Версия core.py: {datetime.fromtimestamp(Path(core.__file__).stat().st_mtime)}")

    cfg = cfgmod.load_settings()
    print(f"Источники даты в настройках: {cfg.get('date_sources')}")

    if len(argv) < 2:
        print("\nУкажите файл или папку:\n  python check_date.py \"D:\\Test\\1392373.jpg\"")
        return 2

    target = Path(argv[1]).expanduser()
    if target.is_dir():
        files = sorted(p for p in target.iterdir() if p.is_file())
        print(f"\nФайлов в папке: {len(files)}")
        for path in files[:40]:
            describe(path, cfg)
        if len(files) > 40:
            print(f"\n...и ещё {len(files) - 40}. Показываю первые 40.")
    elif target.is_file():
        describe(target, cfg)
    else:
        print(f"\nНе найдено: {target}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
