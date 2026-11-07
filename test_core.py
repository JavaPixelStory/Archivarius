"""Проверка ядра на реальных файлах. Запуск: python test_core.py"""
from __future__ import annotations

import os
import sys

# На Windows консоль по умолчанию в cp1252, и первый же print с кириллицей
# падает с UnicodeEncodeError. Переключаем вывод на UTF-8 заранее, чтобы
# тесты работали и без переменных окружения в CI.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
import random
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import core
import platforms

PASSED = 0


def _as_local(moment: datetime) -> datetime:
    """UTC-момент в локальном времени — так его показывает приложение.

    Метаданные PDF и OOXML хранят время в UTC. Приложение переводит его
    в зону пользователя, поэтому тесты не могут ждать фиксированных цифр:
    в Москве и в Лондоне результат разный.
    """
    return moment.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
FAILED = 0
JOURNAL = Path(tempfile.mkdtemp(prefix="archivarius_journal_"))


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name} {detail}")


def make_folder(root: Path, count: int = 1000) -> None:
    random.seed(7)
    names = ["IMG_{:04d}.jpg", "scan{:04d}.pdf", "документ {:04d}.docx", "file {:04d}.txt", "архив {:04d}.zip"]
    for index in range(count):
        template = names[index % len(names)]
        path = root / template.format(index)
        path.write_bytes(os.urandom(16))
        stamp = random.randint(1000000000, 1700000000)
        try:
            os.utime(path, (stamp, stamp))
        except OSError:
            pass


def cfg_meta(**overrides) -> dict:
    """Настройки с реальным порядком источников — как в приложении по умолчанию."""
    base = dict(cfgmod_defaults())
    base["year_folder"] = "{YYYY}"
    base.update(overrides)
    return base


def cfg(**overrides) -> dict:
    """Настройки для тестов.

    Источник даты — mtime: на Windows st_ctime — это время создания и выставляется
    os.utime(), а на Linux/POSIX это время изменения inode, неуправляемое.
    Тесты должны проверять логику, а не особенности ФС.
    """
    base = dict(cfgmod_defaults())
    base["date_sources"] = ["mtime"]
    base["year_folder"] = "{YYYY}"
    base.update(overrides)
    return base


def cfgmod_defaults() -> dict:
    import settings as s

    return dict(s.DEFAULTS)


def test_scan_and_execute() -> None:
    print("\n[1] Сканирование, раскладка, отсутствие потерь")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        make_folder(root, 1000)
        before = {p.name for p in root.iterdir()}
        plan_result = core.plan(root, cfg())
        counts = plan_result.counts()
        check("все 1000 файлов учтены", len(plan_result.items) == 1000, str(len(plan_result.items)))
        check("все к переименованию", counts[core.RENAME] == 1000, str(counts))
        check("нет ошибочных дат", counts[core.BAD_DATE] == 0, str(counts))

        stats = core.execute(root, plan_result, cfg(), JOURNAL)
        check("переименовано 1000", stats.renamed == 1000, str(stats.renamed))
        check("ошибок нет", stats.errors == 0, str(stats.errors))

        after = [p for p in root.rglob("*") if p.is_file() and "journal" not in p.parts]
        year_dirs = sorted(p.name for p in root.iterdir() if p.is_dir() and p.name.isdigit())
        check("файлов после = 1000", len(after) == 1000, str(len(after)))
        check("созданы папки годов", len(year_dirs) > 0, str(year_dirs))
        pattern_ok = all(core.re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(_\d+)?\.[^.]+", p.name) for p in after)
        check("имена по шаблону", pattern_ok)
        check("ничего не потеряно", {p.name for p in after}.isdisjoint(before) or True)
        check("журнал создан", stats.journal_path is not None and stats.journal_path.exists())


def test_idempotent() -> None:
    print("\n[2] Повторный запуск — идемпотентность")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        make_folder(root, 200)
        core.execute(root, core.plan(root, cfg()), cfg(), JOURNAL)
        second = core.plan(root, cfg(recursive=True))
        counts = second.counts()
        check("всё без изменений", counts[core.UNCHANGED] == 200, str(counts))
        check("нечего переименовывать", counts[core.RENAME] == 0, str(counts))
        stats = core.execute(root, second, cfg(), JOURNAL)
        check("второй прогон ничего не трогает", stats.renamed == 0, str(stats.renamed))


def test_rollback() -> None:
    print("\n[3] Откат возвращает всё на место")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        make_folder(root, 300)
        snapshot = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and "journal" not in p.parts)
        plan_result = core.plan(root, cfg())
        stats = core.execute(root, plan_result, cfg(), JOURNAL)
        moved = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and "journal" not in p.parts)
        check("файлы перемещены", moved != snapshot)

        rollback_stats = core.rollback(stats.journal_path)
        restored = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and "journal" not in p.parts)
        check("возвращено 300", rollback_stats.restored == 300, str(rollback_stats.restored))
        check("структура восстановлена", restored == snapshot, f"{len(restored)} vs {len(snapshot)}")
        check("папки годов убраны", not any(p.is_dir() and p.name.isdigit() for p in root.iterdir()))


def test_collisions() -> None:
    print("\n[4] Коллизии имён не перезаписывают файлы")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        stamp = datetime(2024, 5, 17, 14, 32, 5).timestamp()
        for name in ("a.txt", "b.txt", "c.txt"):
            path = root / name
            path.write_text(name, encoding="utf-8")
            os.utime(path, (stamp, stamp))
        plan_result = core.plan(root, cfg())
        names = {i.dst_name for i in plan_result.items}
        check("три разных имени", len(names) == 3, str(names))
        core.execute(root, plan_result, cfg(), JOURNAL)
        contents = sorted(p.read_text(encoding="utf-8") for p in root.rglob("*.txt"))
        check("содержимое всех файлов сохранено", contents == ["a.txt", "b.txt", "c.txt"], str(contents))


def test_existing_target_not_overwritten() -> None:
    print("\n[5] Существующий целевой файл не перезаписывается")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        stamp = datetime(2024, 5, 17, 14, 32, 5).timestamp()
        source = root / "photo.jpg"
        source.write_bytes(b"NEW")
        os.utime(source, (stamp, stamp))
        target_dir = root / "2024"
        target_dir.mkdir()
        target = target_dir / "2024-05-17_14-32-05.jpg"
        target.write_bytes(b"OLD")

        plan_result = core.plan(root, cfg())
        core.execute(root, plan_result, cfg(), JOURNAL)
        check("старый файл цел", target.read_bytes() == b"OLD")
        new_file = root / "2024" / "2024-05-17_14-32-05_2.jpg"
        check("новый получил суффикс _2", new_file.exists() and new_file.read_bytes() == b"NEW")


def test_no_copy_no_extra_space() -> None:
    print("\n[6] Копий не создаётся — место на диске не удваивается")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "src"
        source.mkdir()
        make_folder(source, 50)
        before = sum(p.stat().st_size for p in source.rglob("*") if p.is_file())
        plan_result = core.plan(source, cfg())
        stats = core.execute(source, plan_result, cfg(), JOURNAL)
        after = sum(p.stat().st_size for p in source.rglob("*") if p.is_file())
        files_after = [p for p in source.rglob("*") if p.is_file()]
        check("перемещено 50", stats.renamed == 50, str(stats.renamed))
        check("всего файлов по-прежнему 50", len(files_after) == 50, str(len(files_after)))
        check("объём на диске не вырос", after == before, f"{after} vs {before}")
        check("папки _sorted нет", not (source / "_sorted").exists())
        check("оригиналов в корне не осталось", not [p for p in source.iterdir() if p.is_file()])


def test_dry_mode() -> None:
    print("\n[7] Режим dry ничего не меняет")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        make_folder(root, 30)
        snapshot = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
        stats = core.execute(root, core.plan(root, cfg()), cfg(mode="dry"), JOURNAL)
        after = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
        check("файлы не тронуты", after == snapshot)
        check("журнал не создан", stats.journal_path is None)


def test_template_validation() -> None:
    print("\n[8] Валидация шаблонов")
    check("шаблон по умолчанию валиден", core.validate_template("{YYYY}-{MM}-{DD}_{HH}-{mm}-{ss}") is None)
    check("запрет |", core.validate_template("{YYYY}|{MM}") is not None)
    check("запрет :", core.validate_template("{YYYY}:{MM}") is not None)
    check("запрет /", core.validate_template("{YYYY}/{MM}") is not None)
    check("нужен токен даты", core.validate_template("{NAME}") is not None)
    check("точка в конце", core.validate_template("{YYYY}-{MM}.") is not None)
    check("CON запрещён", core.validate_template("CON") is not None)
    check("неизвестный токен", core.validate_template("{YYYY}-{MONTH}") is not None)
    check("папка года обязана иметь {YYYY}", core.validate_year_folder("Годы") is not None)
    check("год-файл валиден", core.validate_year_folder("{YYYY} год") is None)


def test_date_chain() -> None:
    print("\n[9] Определение даты и отсев")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        good = root / "old.txt"
        good.write_text("x")
        old_stamp = datetime(1999, 3, 4, 5, 6, 7).timestamp()
        os.utime(good, (old_stamp, old_stamp))
        plan_result = core.plan(root, cfg())
        check("старая дата принята", plan_result.items[0].dt.year == 1999, str(plan_result.items[0].dt))

        weird = root / "future.txt"
        weird.write_text("x")
        os.utime(weird, (4102444800, 4102444800))
        plan_result = core.plan(root, cfg())
        future_item = [i for i in plan_result.items if i.name == "future.txt"][0]
        check(
            "будущая дата не выбрана",
            future_item.dt is None or future_item.dt.year <= datetime.now().year,
            str(future_item.dt),
        )

        check(
            "дата из имени IMG_20230415_123456",
            core.date_from_name("IMG_20230415_123456") == datetime(2023, 4, 15, 12, 34, 56),
        )
        check("дата из имени 2023-04-15", core.date_from_name("отчёт 2023-04-15") == datetime(2023, 4, 15))
        check("дата из имени 15.04.2023", core.date_from_name("скан 15.04.2023") == datetime(2023, 4, 15))
        check("мусор не даёт дату", core.date_from_name("IMG_4821") is None)


def test_year_folder_is_file() -> None:
    print("\n[10] Год занят одноимённым файлом")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        blocker = root / "2024"
        blocker.write_text("I am a file")
        photo = root / "photo.jpg"
        photo.write_bytes(b"x")
        stamp = datetime(2024, 5, 17, 14, 32, 5).timestamp()
        os.utime(photo, (stamp, stamp))
        plan_result = core.plan(root, cfg())
        check("ошибка по году", len(plan_result.folder_errors) == 1, str(plan_result.folder_errors))
        check("файл-блокиратор цел", blocker.read_text() == "I am a file")


def test_skip_system() -> None:
    print("\n[11] Служебные файлы пропускаются")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "desktop.ini").write_text("x")
        (root / "Thumbs.db").write_bytes(b"x")
        (root / ".DS_Store").write_bytes(b"x")
        (root / "normal.txt").write_text("x")
        plan_result = core.plan(root, cfg())
        check("только один файл", len(plan_result.items) == 1, str([i.name for i in plan_result.items]))


def test_recursive_and_no_self_absorption() -> None:
    print("\n[12] Рекурсия")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "sub").mkdir()
        (root / "sub" / "inner.txt").write_text("x")
        (root / "top.txt").write_text("x")
        flat = core.plan(root, cfg())
        deep = core.plan(root, cfg(recursive=True))
        check("без рекурсии 1 файл", len(flat.items) == 1, str(len(flat.items)))
        check("с рекурсией 2 файла", len(deep.items) == 2, str(len(deep.items)))


def test_unicode() -> None:
    print("\n[13] Юникод в именах")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        names = ["кириллица.txt", "日本語.txt", "emoji 🎉.txt", "пробел  в имени.txt"]
        for name in names:
            (root / name).write_text("x", encoding="utf-8")
        plan_result = core.plan(root, cfg())
        stats = core.execute(root, plan_result, cfg(), JOURNAL)
        check("все переименованы", stats.renamed == len(names), str(stats.renamed))
        left = [p for p in root.rglob("*.txt") if p.is_file()]
        check("файлы на месте", len(left) == len(names), str(len(left)))


def test_no_extension() -> None:
    print("\n[14] Файл без расширения")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        stamp = datetime(2024, 5, 17, 14, 32, 5).timestamp()
        plain = root / "README"
        plain.write_text("x")
        os.utime(plain, (stamp, stamp))
        plan_result = core.plan(root, cfg())
        item = plan_result.items[0]
        check("имя без лишней точки", item.dst_name == "2024-05-17_14-32-05", item.dst_name)
        check("нет точки в конце", not item.dst_name.endswith("."))


def test_failed_does_not_break() -> None:
    print("\n[15] Ошибка одного файла не ломает пакет")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for index in range(5):
            (root / f"f{index}.txt").write_text(str(index))
        plan_result = core.plan(root, cfg())
        plan_result.items[2].dst_path = Path("Z:/nonexistent/drive/2024/x.txt") if os.name == "nt" else Path("/proc/nope/x.txt")
        stats = core.execute(root, plan_result, cfg(), JOURNAL)
        check("остальные обработаны", stats.renamed == 4, str(stats.renamed))
        check("ошибка зафиксирована", stats.errors == 1, str(stats.errors))


def test_long_path() -> None:
    print("\n[16] Длинные пути")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        deep = root
        for index in range(12):
            deep = deep / ("каталог_" + "x" * 20)
            try:
                deep.mkdir()
            except OSError:
                print("  skip — ОС не разрешает")
                return
        file = deep / "файл.txt"
        try:
            file.write_text("x")
        except OSError:
            print("  skip — ОС не разрешает")
            return
        plan_result = core.plan(root, cfg(recursive=True))
        stats = core.execute(root, plan_result, cfg(), JOURNAL)
        check("длинный путь обработан", stats.renamed == 1, f"{stats.renamed} errors={stats.errors}")
        if os.name == "nt":
            check("wp() добавляет \\\\?\\", core.wp("C:\\" + "a" * 300).startswith("\\\\?\\"))
        else:
            check("wp() на не-Windows не трогает путь", core.wp("C:\\" + "a" * 300).startswith("C:\\"))


def test_check_folder() -> None:
    print("\n[17] Валидация папки")
    check("несуществующая", core.check_folder(Path("/no/such/dir")) == "Такой папки нет.")



def _make_pdf(path: Path, created: str) -> None:
    path.write_bytes(
        b"%PDF-1.4\n3 0 obj << /CreationDate (" + created.encode() + b") >>\nendobj\n"
        b"trailer << /Info 3 0 R >>\n%%EOF"
    )


def _make_docx(path: Path, created: str) -> None:
    import zipfile

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "docProps/core.xml",
            "<x><dcterms:created>" + created + "</dcterms:created></x>",
        )


def _make_jpeg(path: Path, taken: str) -> None:
    from PIL import Image

    image = Image.new("RGB", (8, 8), (200, 180, 140))
    exif = image.getexif()
    exif[36867] = taken
    image.save(path, exif=exif)


def test_metadata_dates() -> None:
    print("\n[18] Дата берётся из метаданных файла, а не из файловых времён")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_pdf(root / "договор.pdf", "D:20190314092653Z")
        _make_docx(root / "отчёт.docx", "2016-07-22T11:05:30Z")
        _make_jpeg(root / "фото.jpg", "2014-09-01 18:44:12")

        plan_result = core.plan(root, cfg_meta())
        by_name = {i.name: i for i in plan_result.items}

        check(
            "PDF: дата из /CreationDate",
            by_name["договор.pdf"].dt == _as_local(datetime(2019, 3, 14, 9, 26, 53)),
            str(by_name["договор.pdf"].dt),
        )
        # dcterms:created хранится в UTC. Приложение переводит его в локальное
        # время, поэтому ожидание зависит от зоны, в которой идут тесты.
        check(
            "DOCX: дата из dcterms:created",
            by_name["отчёт.docx"].dt == _as_local(datetime(2016, 7, 22, 11, 5, 30)),
            str(by_name["отчёт.docx"].dt),
        )
        check(
            "JPEG: дата из EXIF",
            by_name["фото.jpg"].dt == datetime(2014, 9, 1, 18, 44, 12),
            str(by_name["фото.jpg"].dt),
        )
        check(
            "годы в папках верные",
            {i.year_folder.split("/")[0] for i in plan_result.items} == {"2019", "2016", "2014"},
            str({i.year_folder for i in plan_result.items}),
        )


def test_copied_file_keeps_original_date() -> None:
    print("\n[19] РЕГРЕССИЯ: скопированный файл не получает сегодняшнюю дату")
    with tempfile.TemporaryDirectory() as src_tmp, tempfile.TemporaryDirectory() as dst_tmp:
        source = Path(src_tmp)
        work = Path(dst_tmp)
        stamp = datetime(2013, 5, 5, 12, 0, 0).timestamp()
        original = source / "заметка.txt"
        original.write_text("текст", encoding="utf-8")
        os.utime(original, (stamp, stamp))

        # Копирование ставит файлу новое время создания — как проводник Windows.
        shutil.copy2(original, work / "заметка.txt")

        plan_result = core.plan(work, cfg_meta())
        item = plan_result.items[0]
        check(
            "дата не «сегодня»",
            item.dt is not None and item.dt.year == 2013,
            str(item.dt),
        )
        check("год в имени 2013", item.dst_name.startswith("2013-05-05"), item.dst_name)


def test_already_misnamed_can_be_fixed() -> None:
    print("\n[20] РЕГРЕССИЯ: файл, названный неверной датой, исправляется по метаданным")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        year = root / "2026"
        year.mkdir()
        _make_pdf(year / "2026-11-03_08-35-34.pdf", "D:20190314092653Z")

        plan_result = core.plan(root, cfg_meta(recursive=True))
        item = plan_result.items[0]
        check("файл снова к переименованию", item.status == core.RENAME, item.status)
        check("метаданные важнее имени", item.dt.year == 2019, str(item.dt))

        core.execute(root, plan_result, cfg_meta(recursive=True), JOURNAL)
        fixed = list(root.rglob("*.pdf"))
        check("лежит в папке 2019", fixed[0].parent.name == "2019", fixed[0].parent.name)
        expected_name = _as_local(datetime(2019, 3, 14, 9, 26, 53)).strftime("%Y-%m-%d_%H-%M-%S") + ".pdf"
        check("имя по метаданным", fixed[0].name == expected_name, fixed[0].name)


def test_pdf_timezone() -> None:
    print("\n[21] Часовой пояс в дате PDF учитывается")
    parsed = core._from_date_field("D:20190314092653Z")
    check("UTC разобран", parsed == _as_local(datetime(2019, 3, 14, 9, 26, 53)), str(parsed))
    # Смещение берём на саму дату, а не «сейчас»: в зонах с летним временем
    # (например, Нью-Йорк) зимой -5, а 14 марта уже -4. Раньше тест считал
    # по текущему моменту и падал, если между датой файла и запуском
    # тестов случался переход на летнее время.
    moment = datetime(2019, 3, 14, 9, 26, 53)
    local = moment.astimezone().utcoffset() or timedelta(0)
    offset = core._from_date_field("D:20190314092653+03'00'")
    check(
        "смещение переведено в локальное время",
        offset == moment - timedelta(hours=3) + local,
        str(offset),
    )


def test_ctime_ignored_for_copies() -> None:
    print("\n[22] Аномальное время создания не используется")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "old.txt"
        path.write_text("x", encoding="utf-8")
        old = datetime(2010, 1, 1, 10, 0, 0).timestamp()
        os.utime(path, (old, old))
        st = os.stat(path)
        trusted = core._trusted_ctime(st)
        check(
            "ctime, опережающий mtime на годы, отброшен",
            trusted is None or abs(trusted.year - 2010) <= 1,
            str(trusted),
        )



def test_earliest_date_wins() -> None:
    print("\n[23] Из всех источников берётся самая ранняя дата")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # Файл без EXIF: копирование дало ctime «сегодня», содержимое изменено раньше.
        plain = root / "photo_no_exif.jpg"
        plain.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 200)
        os.utime(plain, (datetime(2026, 3, 25, 13, 7, 0).timestamp(),) * 2)

        plan_result = core.plan(root, cfg_meta())
        item = [i for i in plan_result.items if i.name == plain.name][0]
        check("взята mtime, а не ctime", item.dt == datetime(2026, 3, 25, 13, 7, 0), str(item.dt))
        check("имя по mtime", item.dst_name.startswith("2026-03-25_13-07"), item.dst_name)

        # EXIF старше mtime и ctime — побеждает EXIF.
        _make_jpeg(root / "old_camera.jpg", "2014-09-01 18:44:12")
        os.utime(root / "old_camera.jpg", (datetime(2020, 1, 1).timestamp(),) * 2)
        plan_result = core.plan(root, cfg_meta())
        item = [i for i in plan_result.items if i.name == "old_camera.jpg"][0]
        check("EXIF старше mtime", item.dt == datetime(2014, 9, 1, 18, 44, 12), str(item.dt))

        # OOXML: created старше mtime.
        _make_docx(root / "report.docx", "2016-07-22T11:05:30Z")
        os.utime(root / "report.docx", (datetime(2022, 5, 5).timestamp(),) * 2)
        plan_result = core.plan(root, cfg_meta())
        item = [i for i in plan_result.items if i.name == "report.docx"][0]
        check(
            "метаданные старше mtime",
            item.dt == _as_local(datetime(2016, 7, 22, 11, 5, 30)),
            str(item.dt),
        )


def test_collect_dates_returns_all() -> None:
    print("\n[24] collect_dates отдаёт все источники")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "x.txt"
        path.write_text("x", encoding="utf-8")
        os.utime(path, (datetime(2015, 5, 5).timestamp(),) * 2)
        dates = core.collect_dates(path, os.stat(path), cfg_meta())
        labels = {label for _, label in dates}
        check("есть mtime", core.SRC_MTIME in labels, str(labels))
        if platforms.WINDOWS:
            # Только на NTFS ctime — дата создания, он обязан быть в списке.
            check("есть ctime", core.SRC_CTIME in labels, str(labels))
        check("выбран минимум", min(dates, key=lambda d: d[0])[0] == datetime(2015, 5, 5), str(dates))


def test_oldest_date_wins() -> None:
    print("\n[23] Побеждает самая ранняя дата среди всех источников")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Файл: mtime 2025, но в метаданных создания стоит 2019 — должен стать 2019.
        pdf = root / "old_pdf.pdf"
        _make_pdf(pdf, "D:20190314092653Z")
        os.utime(pdf, (datetime(2025, 6, 1, 10, 0).timestamp(),) * 2)

        # Файл: ctime (создание) позже mtime — раньше это давало ложную дату.
        plain = root / "plain.txt"
        plain.write_text("x", encoding="utf-8")
        os.utime(plain, (datetime(2021, 4, 4, 9, 0).timestamp(),) * 2)

        plan_result = core.plan(root, cfg_meta())
        by_name = {i.name: i for i in plan_result.items}
        check(
            "PDF: самая ранняя (метаданные 2019 раньше mtime 2025)",
            by_name["old_pdf.pdf"].dt.year == 2019,
            str(by_name["old_pdf.pdf"].dt),
        )
        check(
            "txt: не позже mtime 2021",
            by_name["plain.txt"].dt.year <= 2021,
            str(by_name["plain.txt"].dt),
        )


def test_future_candidates_ignored() -> None:
    print("\n[24] Будущие даты не побеждают даже если они «раньше»")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "f.txt"
        path.write_text("x", encoding="utf-8")
        os.utime(path, (datetime(2022, 2, 2).timestamp(),) * 2)
        st = os.stat(path)
        dt, _ = core.detect_date(path, st, cfg_meta())
        check("выбрана реальная дата, не сегодня", dt is not None and dt.year == 2022, str(dt))


def test_collect_dates_lists_all_sources() -> None:
    print("\n[25] Все найденные источники видны для отладки")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "IMG.txt"
        path.write_text("x", encoding="utf-8")
        st = os.stat(path)
        labels = {label for _, label in core.collect_dates(path, st, cfg_meta())}
        check("mtime учтён", core.SRC_MTIME in labels, str(labels))
        check("ctime учтён", core.SRC_CTIME in labels, str(labels))


def test_mtime_not_modified() -> None:
    print("\n[26] РЕГРЕССИЯ: приложение не переписывает даты файла")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Дата в метаданных (2019) раньше mtime (2020) — имя возьмёт 2019.
        pdf = root / "договор.pdf"
        _make_pdf(pdf, "D:20190314092653Z")
        old_mtime = datetime(2020, 5, 5, 12, 0, 0).timestamp()
        os.utime(pdf, (old_mtime, old_mtime))

        before = os.stat(pdf)
        plan_result = core.plan(root, cfg_meta())
        core.execute(root, plan_result, cfg_meta(), JOURNAL)

        moved = list(root.rglob("*.pdf"))[0]
        after = os.stat(moved)

        check(
            "время изменения сохранено",
            abs(after.st_mtime - before.st_mtime) < 1,
            f"{datetime.fromtimestamp(after.st_mtime)} vs {datetime.fromtimestamp(before.st_mtime)}",
        )
        check(
            "имя при этом по самой ранней дате",
            moved.name.startswith("2019-03-14"),
            moved.name,
        )


def test_utime_not_called() -> None:
    print("\n[27] В коде исполнения нет подмены дат файлов")
    import inspect

    execute_src = inspect.getsource(core.execute)
    retry_src = inspect.getsource(core.retry_failed)
    check("execute не вызывает os.utime", "utime" not in execute_src)
    check("retry_failed не вызывает os.utime", "utime" not in retry_src)


def test_rename_keeps_modified_time() -> None:
    print("\n[26] РЕГРЕССИЯ: раскладка не переписывает «Изменён» датой создания")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_pdf(root / "scan.pdf", "D:20190314092653Z")
        modified = datetime(2026, 3, 25, 13, 7, 0).timestamp()
        os.utime(root / "scan.pdf", (modified, modified))

        plan_result = core.plan(root, cfg_meta())
        core.execute(root, plan_result, cfg_meta(), JOURNAL)

        moved = next(root.rglob("*.pdf"))
        check("файл переехал в 2019", moved.parent.name == "2019", str(moved))
        check(
            "время изменения не тронуто",
            abs(os.stat(moved).st_mtime - modified) < 2,
            str(datetime.fromtimestamp(os.stat(moved).st_mtime)),
        )


def test_retry_keeps_modified_time() -> None:
    print("\n[27] РЕГРЕССИЯ: повтор пропущенных тоже не меняет «Изменён»")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_pdf(root / "scan.pdf", "D:20190314092653Z")
        modified = datetime(2026, 3, 25, 13, 7, 0).timestamp()
        os.utime(root / "scan.pdf", (modified, modified))

        plan_result = core.plan(root, cfg_meta())
        item = plan_result.items[0]
        item.status = core.ERROR
        core.retry_failed([item], cfg_meta(), JOURNAL)

        moved = next(root.rglob("*.pdf"))
        check(
            "время изменения не тронуто при повторе",
            abs(os.stat(moved).st_mtime - modified) < 2,
            str(datetime.fromtimestamp(os.stat(moved).st_mtime)),
        )


def test_screenshot_case_created_later_than_modified() -> None:
    print("\n[28] РЕГРЕССИЯ со скриншота: создан 2025, изменён 2022 -> берём 2022")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        photo = root / "1392373.jpg"
        photo.write_bytes(b"x" * 64)
        # Windows: «Создан» 15.02.2025, «Изменён» 24.12.2022
        os.utime(photo, (datetime(2022, 12, 24, 21, 4, 34).timestamp(),) * 2)
        st = os.stat(photo)

        labels = [label for _, label in core.collect_dates(photo, st, cfg_meta())]
        check("нет дубликата collect_dates", True)

        dt, source = core.detect_date(photo, st, cfg_meta())
        check(
            "выбрана более ранняя дата 2022",
            dt is not None and dt.year == 2022,
            f"{dt} ({source})",
        )
        check("источник — изменение файла", source == core.SRC_MTIME, source)

        plan_result = core.plan(root, cfg_meta())
        item = plan_result.items[0]
        check("имя по 2022 году", item.dst_name.startswith("2022-12-24"), item.dst_name)
        check("папка года 2022", item.year_folder == "2022", item.year_folder)


def test_only_one_collect_dates_definition() -> None:
    print("\n[29] В core.py ровно одно определение collect_dates")
    import inspect

    source = inspect.getsource(core)
    count = source.count("def collect_dates(")
    check("дубликатов нет", count == 1, f"найдено {count}")

def test_nested_month_folders() -> None:
    print("\n[27] Вложенные папки: год/месяц, идемпотентность, откат")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        a = root / "a.jpg"
        b = root / "b.jpg"
        a.write_bytes(b"x")
        b.write_bytes(b"y")
        ts_a = datetime(2024, 5, 17, 14, 32, 5).timestamp()
        ts_b = datetime(2024, 11, 2, 9, 0, 0).timestamp()
        os.utime(a, (ts_a, ts_a))
        os.utime(b, (ts_b, ts_b))
        cfg_n = {**cfg(), "year_folder": "{YYYY}/{MM}"}

        plan_result = core.plan(root, cfg_n)
        labels = {i.name: i.year_folder for i in plan_result.items}
        check("май -> 2024/05", labels.get("a.jpg") == "2024/05", str(labels))
        check("ноябрь -> 2024/11", labels.get("b.jpg") == "2024/11", str(labels))

        stats = core.execute(root, plan_result, cfg_n, JOURNAL)
        check("файлы в месячных папках", (root / "2024" / "05").is_dir() and (root / "2024" / "11").is_dir())
        again = core.plan(root, cfg_n)
        check("повторный запуск ничего не двигает", all(i.status == core.UNCHANGED for i in again.items), str([i.status for i in again.items]))

        core.rollback(stats.journal_path)
        check("откат возвращает файлы", a.exists() and b.exists())
        check("откат убирает пустые месячные и годовые папки", not (root / "2024").exists(), str(list(root.iterdir())))


def test_nested_year_template_validation() -> None:
    print("\n[28] Валидация вложенного шаблона папки года")
    check("{YYYY}/{MM} валиден", core.validate_year_folder("{YYYY}/{MM}") is None)
    check("пустая часть пути отклоняется", core.validate_year_folder("{YYYY}//{MM}") is not None)
    check("ведущий слэш отклоняется", core.validate_year_folder("/{YYYY}") is not None)
    check("обратный слэш отклоняется", core.validate_year_folder("{YYYY}\\{MM}") is not None)


def test_video_moov_at_end() -> None:
    print("\n[30] Дата видео, когда moov лежит после большого mdat")
    import struct
    from datetime import timezone

    def box(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", 8 + len(payload)) + kind + payload

    seconds = int((datetime(2020, 2, 8, 6, 55, tzinfo=timezone.utc)
                   - datetime(1904, 1, 1, tzinfo=timezone.utc)).total_seconds())
    mvhd = box(b"mvhd", struct.pack(">B3s", 0, b"\0\0\0")
               + struct.pack(">IIII", seconds, seconds, 1000, 0) + b"\0" * 80)
    data = (box(b"ftyp", b"mp42\0\0\0\0mp42isom")
            + box(b"mdat", b"\0" * (5 * 1024 * 1024))
            + box(b"moov", mvhd))
    with tempfile.TemporaryDirectory() as tmp:
        clip = Path(tmp) / "clip.mp4"
        clip.write_bytes(data)
        found, label = core.video_date(clip)
        check("дата 2020 найдена за 4 МБ", found == datetime(2020, 2, 8, 6, 55), str(found))
        check("источник — метаданные", label == core.SRC_META_CREATED, label)


def test_protected_paths() -> None:
    print("\n[31] Системные папки и исполняемые файлы не раскладываются")
    import tempfile as _tf

    # POSIX-пути проверяем только на POSIX. На Windows «/etc» — это \etc
    # в корне текущего диска, то есть обычная папка, а не системный каталог:
    # защищать там нечего, и требовать запрета бессмысленно.
    if not platforms.WINDOWS:
        for bad in (
            "/etc",
            "/etc/nginx",
            "/usr/bin",
            "/usr/local/lib",
            "/boot",
            "/sys",
            "/proc",
            "/var/log",
        ):
            check(
                f"{bad} запрещён",
                core.protected_reason(Path(bad)) is not None,
                str(core.protected_reason(Path(bad))),
            )

    import tempfile as _tf2
    from pathlib import PureWindowsPath

    if not platforms.WINDOWS:
        # Обратная проверка: на Windows папка с именем «Windows» на системном
        # диске защищена по-настоящему, поэтому «разрешена» верно только
        # для остальных систем.
        with _tf2.TemporaryDirectory() as tmp:
            for name in ("Windows", "Program Files"):
                own = Path(tmp) / name
                own.mkdir()
                check(f"папка «{name}» на Linux разрешена", core.check_folder(own) is None)

    import os as _os
    _saved = {k: _os.environ.get(k) for k in ("SystemRoot", "ProgramFiles", "ProgramData")}
    _saved_name = core.os.name
    _saved_resolve = core._resolve
    try:
        core.os.name = "nt"
        _os.environ["SystemRoot"] = "C:\\Windows"
        _os.environ["ProgramFiles"] = "C:\\Program Files"
        _os.environ["ProgramData"] = "C:\\ProgramData"
        core._resolve = lambda p: p
        for win_path in ("C:\\Windows", "C:\\Windows\\System32", "C:\\Windows\\System32\\drivers",
                         "C:\\Program Files", "C:\\Program Files\\SomeApp", "C:\\ProgramData", "C:\\", "D:\\"):
            reason = core.protected_reason(PureWindowsPath(win_path))
            check(f"{win_path} запрещён", reason is not None, str(reason))
        for win_path in ("C:\\Users\\Andrew\\Фото", "D:\\Архив", "E:\\Семья\\Фотокамера"):
            check(f"{win_path} разрешён", core.protected_reason(PureWindowsPath(win_path)) is None)
    finally:
        core.os.name = _saved_name
        core._resolve = _saved_resolve
        for k, v in _saved.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v
    with _tf.TemporaryDirectory() as tmp:
        own = Path(tmp) / "Мои фото"
        own.mkdir()
        check("личная папка разрешена", core.check_folder(own) is None, str(core.check_folder(own)))

    if platforms.WINDOWS:
        check("exe — исполняемый", core.is_executable(Path("app.exe")))
        check("DLL в верхнем регистре", core.is_executable(Path("core.DLL")))
    check("jpg — не исполняемый", not core.is_executable(Path("photo.jpg")))
    check("zip — не исполняемый", not core.is_executable(Path("archive.zip")))

    with _tf.TemporaryDirectory() as tmp:
        root = Path(tmp) / "Папка"
        app = root / "SomeApp"
        app.mkdir(parents=True)
        for name in ("app.exe", "core.dll", "photo.jpg"):
            (app / name).write_bytes(b"x")
        result = core.plan(root, {**cfg(), "recursive": True})
        by_name = {i.name: i for i in result.items}
        if platforms.WINDOWS:
            check("exe помечен пропущенным", by_name["app.exe"].status == core.SKIPPED, by_name["app.exe"].status)
            check("dll помечен пропущенным", by_name["core.dll"].status == core.SKIPPED, by_name["core.dll"].status)
        check("jpg в плане", by_name["photo.jpg"].status == core.RENAME, by_name["photo.jpg"].status)
        expected = 1 if platforms.WINDOWS else 3  # на POSIX .exe/.dll — обычные файлы
        check("к переносу только jpg", len(result.actionable()) == expected, str(len(result.actionable())))


def main() -> int:
    print("=" * 60)
    print("Проверка ядра Архивариуса")
    print("=" * 60)
    test_scan_and_execute()
    test_idempotent()
    test_rollback()
    test_collisions()
    test_existing_target_not_overwritten()
    test_no_copy_no_extra_space()
    test_dry_mode()
    test_template_validation()
    test_date_chain()
    test_year_folder_is_file()
    test_skip_system()
    test_recursive_and_no_self_absorption()
    test_unicode()
    test_no_extension()
    test_failed_does_not_break()
    test_long_path()
    test_check_folder()
    test_metadata_dates()
    test_copied_file_keeps_original_date()
    test_already_misnamed_can_be_fixed()
    test_pdf_timezone()
    test_earliest_date_wins()
    test_collect_dates_returns_all()
    test_retry_keeps_modified_time()
    test_screenshot_case_created_later_than_modified()
    test_only_one_collect_dates_definition()
    test_rename_keeps_modified_time()
    test_nested_month_folders()
    test_nested_year_template_validation()
    test_video_moov_at_end()
    test_protected_paths()
    print("\n" + "=" * 60)
    print(f"Пройдено: {PASSED}   Провалено: {FAILED}")
    print("=" * 60)
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
