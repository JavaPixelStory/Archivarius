"""Canvas-примитивы для интерфейса «Архивариуса».

Tkinter не умеет скруглять углы у штатных виджетов, поэтому кнопки, поля и
зоны рисуются на Canvas.

Чекбоксы и радиокнопки рендерятся через Pillow в PNG с антиалиасингом:
встроенные фигуры Tk дают рваные края, а растровые картинки выглядят гладко.

Заглушки иконок рисуются векторно прямо на Canvas. Если рядом с приложением
есть файл assets/icons/<имя>.png, он подставляется вместо заглушки.
"""

from __future__ import annotations

import math
import tkinter as tk
from pathlib import Path
from typing import Callable

import fonts

# --- палитра из макета -------------------------------------------------------
WHITE = "#FFFFFF"
GREEN = "#2F7D32"
GREEN_DARK = "#26632A"
GREEN_HOVER = "#3A8F3E"
GREEN_SOFT = "#EBF2EB"
GREEN_SOFT_HOVER = "#E0EDE0"
INK = "#1F2A20"
MUTED = "#6B7B6E"
GOLD = "#FFDAAC"
GOLD_HOVER = "#FFE7C8"
LINE_SOFT = "#D7E4D7"
DISABLED_BG = "#B9D2BA"
DISABLED_TEXT = "#EFE6D2"

# Радиусы скруглений. Меняются здесь — влияют на все экраны сразу.
RADIUS_BIG = 30  # крупные кнопки (высота 46 px и больше)
RADIUS_SMALL = 20  # мелкие кнопки и чипы фильтров
RADIUS_FIELD = 20  # поля ввода
RADIUS_ZONE = 20  # зона перетаскивания
RADIUS_ZONE_BACK = 40  # зона перетаскивания бэкграунд

_ICON_CACHE: dict[tuple[str, int], tk.PhotoImage | None] = {}
_TOGGLE_CACHE: dict[tuple[str, bool, int], tk.PhotoImage | None] = {}


def _assets_dir() -> Path:
    try:
        import settings as cfgmod

        return cfgmod.assets_dir()
    except Exception:  # noqa: BLE001 - модуль может быть ещё не импортирован
        return Path(__file__).resolve().parent / "assets"


def load_icon(name: str, size: int) -> tk.PhotoImage | None:
    """PNG-иконка из assets/icons. Нет файла — вернёт None, нарисуется заглушка."""
    key = (name, size)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    path = _assets_dir() / "icons" / f"{name}.png"
    photo: tk.PhotoImage | None = None
    if path.is_file():
        try:
            from PIL import Image, ImageTk

            with Image.open(path) as image:
                image = image.convert("RGBA")
                image.thumbnail((size, size), Image.LANCZOS)
                photo = ImageTk.PhotoImage(image)
        except Exception:  # noqa: BLE001 - битая иконка не должна ломать экран
            photo = None
    _ICON_CACHE[key] = photo
    return photo


def load_badge(name: str, size: int = 22, bg: str = GREEN, radius: int = 7) -> tk.PhotoImage | None:
    """Значок на цветной подложке со скруглёнными углами.

    Сами файлы в assets/icons нарисованы золотом — на белом фоне справки
    они почти не видны. Здесь подложка рисуется Pillow, а иконка кладётся
    сверху перекрашенной в белый (альфа-канал сохраняется).
    """
    key = (f"badge:{name}:{bg}:{radius}", size)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]

    photo: tk.PhotoImage | None = None
    try:
        from PIL import Image, ImageDraw, ImageTk

        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(canvas).rounded_rectangle(
            [0, 0, size - 1, size - 1], radius=radius, fill=bg
        )

        path = _assets_dir() / "icons" / f"{name}.png"
        if path.is_file():
            with Image.open(path) as source:
                glyph = source.convert("RGBA")
                glyph.thumbnail((size - 8, size - 8), Image.LANCZOS)
                white = Image.new("RGBA", glyph.size, (255, 255, 255, 255))
                white.putalpha(glyph.split()[3])
                canvas.paste(
                    white,
                    ((size - glyph.width) // 2, (size - glyph.height) // 2),
                    white,
                )
        photo = ImageTk.PhotoImage(canvas)
    except Exception:  # noqa: BLE001 - значок не должен ломать экран
        photo = None

    _ICON_CACHE[key] = photo
    return photo


def toggle_image(kind: str, checked: bool, size: int = 24) -> tk.PhotoImage | None:
    """Чекбокс или радиокнопка, отрисованные Pillow с антиалиасингом.

    kind: "check" | "radio". Рисуем в 4× и уменьшаем — края получаются гладкими.
    """
    key = (kind, checked, size)
    if key in _TOGGLE_CACHE:
        return _TOGGLE_CACHE[key]
    try:
        from PIL import Image, ImageDraw, ImageTk

        scale = 4
        s = size * scale
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        stroke = max(2, int(s * 0.075))

        if kind == "check":
            pad = int(s * 0.10)
            radius = int(s * 0.22)
            box = (pad, pad, s - pad, s - pad)
            if checked:
                d.rounded_rectangle(box, radius=radius, fill=GREEN)
                # галочка
                w = max(3, int(s * 0.11))
                d.line(
                    [(s * 0.27, s * 0.52), (s * 0.44, s * 0.69)],
                    fill=WHITE,
                    width=w,
                    joint="curve",
                )
                d.line(
                    [(s * 0.44, s * 0.69), (s * 0.74, s * 0.33)],
                    fill=WHITE,
                    width=w,
                    joint="curve",
                )
            else:
                d.rounded_rectangle(box, radius=radius, outline="#8FA88F", width=stroke)
        else:
            pad = int(s * 0.08)
            if checked:
                d.ellipse((pad, pad, s - pad, s - pad), fill=GREEN)
                inner = int(s * 0.30)
                c = s // 2
                d.ellipse((c - inner, c - inner, c + inner, c + inner), fill=WHITE)
            else:
                d.ellipse((pad, pad, s - pad, s - pad), outline="#8FA88F", width=stroke)

        img = img.resize((size, size), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        _TOGGLE_CACHE[key] = photo
        return photo
    except Exception:  # noqa: BLE001
        _TOGGLE_CACHE[key] = None
        return None


def draw_icon(
    canvas: tk.Canvas,
    name: str,
    cx: float,
    cy: float,
    size: float,
    color: str = WHITE,
    tags: str = "icon",
) -> None:
    """Заглушка иконки, нарисованная векторно.

    Замена: положить PNG с тем же именем в assets/icons.
    """
    s = size
    w = max(2.0, s * 0.14)

    if name == "icon_settings":
        r_out = s * 0.36
        r_in = s * 0.30
        for i in range(8):
            ang = math.pi * 2 * i / 8
            canvas.create_line(
                cx + math.cos(ang) * r_in,
                cy + math.sin(ang) * r_in,
                cx + math.cos(ang) * r_out,
                cy + math.sin(ang) * r_out,
                fill=color,
                width=w * 1.5,
                capstyle="round",
                tags=tags,
            )
        rr = s * 0.24
        canvas.create_oval(
            cx - rr, cy - rr, cx + rr, cy + rr, outline=color, width=w, tags=tags
        )

    elif name in ("icon_folder", "icon_folder_big"):
        hw, hh = s * 0.42, s * 0.15
        top = cy - hh * 0.55
        canvas.create_line(
            cx - hw, top + hh, cx - hw * 0.25, top + hh, fill=color, width=w, tags=tags
        )
        canvas.create_line(
            cx - hw * 0.25,
            top + hh,
            cx - hw * 0.12,
            top,
            fill=color,
            width=w,
            tags=tags,
        )
        canvas.create_line(
            cx - hw * 0.12, top, cx + hw, top, fill=color, width=w, tags=tags
        )
        canvas.create_line(
            cx + hw, top, cx + hw, top + hh * 2, fill=color, width=w, tags=tags
        )
        canvas.create_line(
            cx - hw, top + hh, cx - hw, top + hh * 2, fill=color, width=w, tags=tags
        )
        canvas.create_line(
            cx - hw, top + hh * 2, cx + hw, top + hh * 2, fill=color, width=w, tags=tags
        )
        canvas.create_line(
            cx - hw, top + hh, cx + hw, top + hh, fill=color, width=w, tags=tags
        )

    elif name == "icon_wand":
        canvas.create_line(
            cx - s * 0.30,
            cy + s * 0.32,
            cx + s * 0.18,
            cy - s * 0.16,
            fill=color,
            width=w * 1.4,
            capstyle="round",
            tags=tags,
        )
        for dx, dy, rr in (
            (0.34, -0.30, 0.055),
            (0.24, 0.02, 0.038),
            (0.06, -0.36, 0.038),
        ):
            px, py = cx + s * dx, cy + s * dy
            canvas.create_oval(
                px - s * rr,
                py - s * rr,
                px + s * rr,
                py + s * rr,
                fill=GOLD,
                outline="",
                tags=tags,
            )

    elif name == "icon_back":
        canvas.create_line(
            cx + s * 0.26,
            cy,
            cx - s * 0.26,
            cy,
            fill=color,
            width=w,
            capstyle="round",
            tags=tags,
        )
        canvas.create_line(
            cx - s * 0.26,
            cy,
            cx - s * 0.02,
            cy - s * 0.22,
            fill=color,
            width=w,
            capstyle="round",
            tags=tags,
        )
        canvas.create_line(
            cx - s * 0.26,
            cy,
            cx - s * 0.02,
            cy + s * 0.22,
            fill=color,
            width=w,
            capstyle="round",
            tags=tags,
        )

    elif name == "icon_check":
        canvas.create_line(
            cx - s * 0.26,
            cy + s * 0.02,
            cx - s * 0.06,
            cy + s * 0.22,
            fill=color,
            width=w * 1.2,
            capstyle="round",
            tags=tags,
        )
        canvas.create_line(
            cx - s * 0.06,
            cy + s * 0.22,
            cx + s * 0.28,
            cy - s * 0.22,
            fill=color,
            width=w * 1.2,
            capstyle="round",
            tags=tags,
        )

    elif name == "icon_undo":
        r = s * 0.30
        canvas.create_arc(
            cx - r,
            cy - r,
            cx + r,
            cy + r,
            start=30,
            extent=300,
            style="arc",
            outline=color,
            width=w,
            tags=tags,
        )
        canvas.create_line(
            cx - r * 1.15,
            cy - r * 0.55,
            cx - r * 0.45,
            cy - r * 0.72,
            fill=color,
            width=w,
            capstyle="round",
            tags=tags,
        )

    elif name == "icon_clock":
        r = s * 0.34
        canvas.create_oval(
            cx - r, cy - r, cx + r, cy + r, outline=color, width=w, tags=tags
        )
        canvas.create_line(
            cx, cy - r * 0.5, cx, cy, fill=color, width=w, capstyle="round", tags=tags
        )
        canvas.create_line(
            cx,
            cy,
            cx + r * 0.45,
            cy + r * 0.28,
            fill=color,
            width=w,
            capstyle="round",
            tags=tags,
        )

    elif name == "icon_journal":
        hw, hh = s * 0.28, s * 0.34
        canvas.create_line(
            cx - hw, cy - hh, cx + hw, cy - hh, fill=color, width=w, tags=tags
        )
        canvas.create_line(
            cx + hw, cy - hh, cx + hw, cy + hh, fill=color, width=w, tags=tags
        )
        canvas.create_line(
            cx + hw, cy + hh, cx - hw, cy + hh, fill=color, width=w, tags=tags
        )
        canvas.create_line(
            cx - hw, cy + hh, cx - hw, cy - hh, fill=color, width=w, tags=tags
        )
        canvas.create_line(
            cx - hw * 0.35,
            cy - hh,
            cx - hw * 0.35,
            cy + hh,
            fill=color,
            width=w * 0.8,
            tags=tags,
        )

    elif name == "icon_info":
        # Кружок с точкой и вертикальной чертой — стандартный знак «i».
        r = s * 0.42
        canvas.create_oval(
            cx - r, cy - r, cx + r, cy + r, outline=color, width=w, tags=tags
        )
        canvas.create_oval(
            cx - w * 0.55,
            cy - s * 0.19,
            cx + w * 0.55,
            cy - s * 0.19 + w * 1.1,
            fill=color,
            outline="",
            tags=tags,
        )
        canvas.create_line(
            cx,
            cy - s * 0.05,
            cx,
            cy + s * 0.22,
            fill=color,
            width=w,
            capstyle="round",
            tags=tags,
        )

    elif name == "icon_drop":
        canvas.create_line(
            cx,
            cy - s * 0.28,
            cx,
            cy + s * 0.10,
            fill=color,
            width=w,
            capstyle="round",
            tags=tags,
        )
        canvas.create_line(
            cx,
            cy - s * 0.28,
            cx - s * 0.16,
            cy - s * 0.10,
            fill=color,
            width=w,
            capstyle="round",
            tags=tags,
        )
        canvas.create_line(
            cx,
            cy - s * 0.28,
            cx + s * 0.16,
            cy - s * 0.10,
            fill=color,
            width=w,
            capstyle="round",
            tags=tags,
        )
        canvas.create_line(
            cx - s * 0.28,
            cy + s * 0.26,
            cx + s * 0.28,
            cy + s * 0.26,
            fill=color,
            width=w,
            capstyle="round",
            tags=tags,
        )


def _round_points(x1: float, y1: float, x2: float, y2: float, r: float) -> list[float]:
    return [
        x1 + r,
        y1,
        x1 + r,
        y1,
        x2 - r,
        y1,
        x2 - r,
        y1,
        x2,
        y1,
        x2,
        y1 + r,
        x2,
        y1 + r,
        x2,
        y2 - r,
        x2,
        y2 - r,
        x2,
        y2,
        x2 - r,
        y2,
        x2 - r,
        y2,
        x1 + r,
        y2,
        x1 + r,
        y2,
        x1,
        y2,
        x1,
        y2 - r,
        x1,
        y2 - r,
        x1,
        y1 + r,
        x1,
        y1 + r,
        x1,
        y1,
    ]


def round_rect(
    canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, r: float, **kwargs
):
    r = max(2.0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return canvas.create_polygon(
        _round_points(x1, y1, x2, y2, r), smooth=True, **kwargs
    )


def dashed_round_rect(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    r: float,
    color: str = GREEN,
    width: int = 2,
    dash: tuple[int, int] = (7, 5),
    tags: str = "",
) -> None:
    """Пунктирная рамка со скруглёнными углами: 4 дуги + 4 отрезка."""
    d = 2 * r
    kw = dict(outline=color, width=width, style="arc", dash=dash, tags=tags)
    canvas.create_arc(x1, y1, x1 + d, y1 + d, start=90, extent=90, **kw)
    canvas.create_arc(x2 - d, y1, x2, y1 + d, start=0, extent=90, **kw)
    canvas.create_arc(x2 - d, y2 - d, x2, y2, start=270, extent=90, **kw)
    canvas.create_arc(x1, y2 - d, x1 + d, y2, start=180, extent=90, **kw)

    line_kw = dict(fill=color, width=width, dash=dash, tags=tags)
    canvas.create_line(x1 + r, y1, x2 - r, y1, **line_kw)
    canvas.create_line(x2, y1 + r, x2, y2 - r, **line_kw)
    canvas.create_line(x1 + r, y2, x2 - r, y2, **line_kw)
    canvas.create_line(x1, y1 + r, x1, y2 - r, **line_kw)


def font(size: int, weight: str = "normal") -> tuple[str, int, str]:
    """Poppins, если загрузился; иначе системный шрифт Tk."""
    return (fonts.family(), size, fonts.weight(weight))


class RoundButton(tk.Canvas):
    """Кнопка со скруглёнными углами.

    kind: "primary" — зелёная с золотым текстом, "secondary" — белая с зелёным
    контуром, "ghost" — без заливки, подсветка при наведении.
    """

    def __init__(
        self,
        master,
        label: str,
        command: Callable[[], None] | None = None,
        kind: str = "primary",
        width: int = 200,
        height: int = 52,
        icon: str | None = None,
        font_size: int = 12,
        radius: int | None = None,
        bg: str = WHITE,
        icon_color: str | None = None,
        **kwargs,
    ):
        super().__init__(
            master,
            width=width,
            height=height,
            bg=bg,
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        self._label = label
        self._command = command
        self._kind = kind
        self._bw = width
        self._bh = height
        self._icon = icon
        # Свой цвет значка поверх стандартного для этого kind.
        self._icon_color = icon_color
        self._font_size = font_size
        # Большие кнопки — 16, маленькие — 8.
        self._radius = (
            radius
            if radius is not None
            else (RADIUS_BIG if height >= 46 else RADIUS_SMALL)
        )
        self._enabled = True
        self._hover = False
        self._pressed = False

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.configure(cursor="hand2")
        self._draw()

    # --- состояние ---
    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if not enabled:
            self._hover = False
        self.configure(cursor="hand2" if enabled else "")
        self._draw()

    def is_enabled(self) -> bool:
        return self._enabled

    def set_text(self, text: str) -> None:
        self._label = text
        self._draw()

    def get_text(self) -> str:
        return self._label

    # --- события ---
    def _on_enter(self, _e=None):
        if self._enabled:
            self._hover = True
            self._draw()

    def _on_leave(self, _e=None):
        self._hover = False
        self._pressed = False
        self._draw()

    def _on_press(self, _e=None):
        if self._enabled:
            self._pressed = True
            self._draw()

    def _on_release(self, _e=None):
        if not self._enabled:
            return
        was = self._pressed
        self._pressed = False
        self._draw()
        if was and self._command is not None:
            self._command()

    # --- отрисовка ---
    def _colors(self) -> tuple[str, str]:
        """Возвращает (заливка, цвет текста)."""
        if not self._enabled:
            if self._kind == "primary":
                return DISABLED_BG, DISABLED_TEXT
            return WHITE, "#9FB8A0"
        if self._kind == "primary":
            fill = (
                GREEN_DARK if self._pressed else (GREEN_HOVER if self._hover else GREEN)
            )
            return fill, GOLD
        if self._kind == "secondary":
            fill = GREEN_SOFT if (self._hover or self._pressed) else WHITE
            return fill, GREEN
        # ghost
        fill = GREEN_SOFT if (self._hover or self._pressed) else self.cget("bg")
        return fill, GREEN

    def _draw(self) -> None:
        self.delete("all")
        fill, text_color = self._colors()
        pad = 1

        if self._kind == "ghost":
            if self._hover or self._pressed:
                round_rect(
                    self,
                    pad,
                    pad,
                    self._bw - pad,
                    self._bh - pad,
                    self._radius,
                    fill=fill,
                    outline="",
                )
        else:
            outline = (
                ""
                if self._kind == "primary"
                else (LINE_SOFT if self._enabled else "#CFE0D0")
            )
            round_rect(
                self,
                pad,
                pad,
                self._bw - pad,
                self._bh - pad,
                self._radius,
                fill=fill,
                outline=outline,
                width=1 if outline else 0,
            )

        # Значок рисуем своим цветом, если задан, иначе цветом из kind.
        icon_paint = self._icon_color or text_color

        # Кнопка только с иконкой: подписи нет, центрируем сам значок.
        if not self._label and self._icon:
            icon_only = 24
            photo_only = load_icon(self._icon, icon_only)
            if photo_only is not None:
                self.create_image(self._bw / 2, self._bh / 2, image=photo_only)
            else:
                draw_icon(
                    self,
                    self._icon,
                    self._bw / 2,
                    self._bh / 2,
                    icon_only,
                    color=icon_paint,
                )
            return

        icon_size = int(min(self._bh * 0.44, 24))
        # На кнопках Poppins Regular, без жирного начертания.
        text_font = font(self._font_size, "normal")

        if not self._icon:
            self.create_text(
                self._bw / 2,
                self._bh / 2,
                text=self._label,
                fill=text_color,
                font=text_font,
            )
            return

        probe = self.create_text(
            -2000, -2000, text=self._label, fill=text_color, font=text_font, anchor="w"
        )
        bbox = self.bbox(probe)
        text_width = (
            (bbox[2] - bbox[0]) if bbox else len(self._label) * self._font_size * 0.6
        )
        self.delete(probe)

        gap = icon_size * 0.45
        total = icon_size + gap + text_width
        left = (self._bw - total) / 2
        icon_cx = left + icon_size / 2
        text_x = left + icon_size + gap

        photo = load_icon(self._icon, icon_size) if self._icon else None
        if photo is not None:
            self.create_image(icon_cx, self._bh / 2, image=photo)
        else:
            draw_icon(
                self, self._icon, icon_cx, self._bh / 2, icon_size, color=icon_paint
            )
        self.create_text(
            text_x,
            self._bh / 2,
            text=self._label,
            fill=text_color,
            font=text_font,
            anchor="w",
        )


class RoundedSelect(tk.Frame):
    """Выпадающий список на скруглённой подложке.

    Штатный ttk.Combobox скруглять нельзя: он рисуется средствами системной
    темы и параметра радиуса не имеет. Здесь подложка, текст и стрелка
    нарисованы на Canvas, а список открывается отдельным окном той же ширины.
    """

    def __init__(
        self,
        master,
        textvariable: tk.StringVar,
        values: list[str] | None = None,
        width: int = 440,
        height: int = 40,
        font_size: int = 11,
        radius: int = RADIUS_FIELD,
        bg: str = WHITE,
        placeholder: str = "—",
        command=None,
        **kwargs,
    ):
        super().__init__(master, bg=bg, **kwargs)
        self._bw = width
        self._bh = height
        self._radius = radius
        self._var = textvariable
        self._values: list[str] = list(values or [])
        self._placeholder = placeholder
        self._command = command
        self._font = font(font_size)

        self.canvas = tk.Canvas(
            self, width=width, height=height, bg=bg, highlightthickness=0, bd=0
        )
        self.canvas.pack()
        self.canvas.configure(cursor="hand2")
        self.canvas.bind("<Button-1>", lambda _e: self._open_menu())

        self._popup: tk.Toplevel | None = None

        self._draw()

    # --- отрисовка ---------------------------------------------------
    def _draw(self) -> None:
        self.canvas.delete("all")
        round_rect(
            self.canvas,
            1,
            1,
            self._bw - 1,
            self._bh - 1,
            self._radius,
            fill=GREEN_SOFT,
            outline="",
        )

        text = self._var.get() or self._placeholder
        text = self._truncate(text, self._bw - 64)
        color = GREEN if self._var.get() else MUTED
        self.canvas.create_text(
            16, self._bh / 2, text=text, anchor="w", fill=color, font=self._font
        )

        # Стрелка вниз: в Roboto нет подходящего символа, рисуем линиями.
        ax, ay = self._bw - 26, self._bh / 2 - 3
        self.canvas.create_line(ax, ay, ax + 8, ay + 7, fill=GREEN, width=2)
        self.canvas.create_line(ax + 8, ay + 7, ax + 16, ay, fill=GREEN, width=2)

    def _truncate(self, text: str, max_w: float) -> str:
        """Обрезает длинный путь многоточием, чтобы влез в поле."""
        if self.canvas.tk.call("font", "measure", self._font, text) <= max_w:
            return text
        cut = text
        while (
            cut
            and self.canvas.tk.call("font", "measure", self._font, cut + "…") > max_w
        ):
            cut = cut[:-1]
        return cut + "…"

    # --- меню --------------------------------------------------------
    def _open_menu(self) -> None:
        if not self._values:
            return
        self._close_menu()
        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.configure(bg=LINE_SOFT)
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + 2
        rows = min(len(self._values), 8)
        popup.geometry(f"{self._bw}x{rows * 30 + 2}+{x}+{y}")

        box = tk.Listbox(
            popup,
            bd=0,
            highlightthickness=0,
            relief="flat",
            activestyle="none",
            exportselection=False,
            bg=WHITE,
            fg=INK,
            selectbackground=GREEN_SOFT,
            selectforeground=GREEN,
            font=self._font,
        )
        box.pack(fill="both", expand=True, padx=1, pady=1)
        for value in self._values:
            box.insert("end", self._truncate(value, self._bw - 32))
        box.bind(
            "<ButtonRelease-1>",
            lambda _e: self._pick_index(
                box.nearest(box.winfo_pointery() - box.winfo_rooty())
            ),
        )
        box.bind("<Return>", lambda _e: self._pick_index(box.index("active")))
        box.bind("<Escape>", lambda _e: self._close_menu())

        popup.bind(
            "<FocusOut>", lambda _e: self.after(50, self._close_menu_if_unfocused)
        )
        self._popup = popup
        popup.focus_force()
        box.focus_set()

    def _close_menu_if_unfocused(self) -> None:
        if self._popup is not None and self._popup.focus_get() is None:
            self._close_menu()

    def _close_menu(self) -> None:
        if self._popup is not None:
            self._popup.destroy()
            self._popup = None

    def _pick_index(self, index: int) -> None:
        if 0 <= index < len(self._values):
            self._pick(self._values[index])
        self._close_menu()

    def _pick(self, value: str) -> None:
        self._var.set(value)
        self._draw()
        if self._command is not None:
            self._command(value)

    # --- интерфейс, совместимый с Combobox ---------------------------
    def get(self) -> str:
        return self._var.get()

    def set_values(self, values: list[str], select_first: bool = False) -> None:
        self._values = list(values)
        if select_first and self._values:
            self._var.set(self._values[0])  # без вызова command
        elif not self._values:
            self._var.set("")
        self._draw()

    def select_index(self, index: int) -> None:
        if 0 <= index < len(self._values):
            self._var.set(self._values[index])
            self._draw()


class Field(tk.Frame):
    """Поле ввода на светло-зелёной скруглённой подложке.

    Текст внутри — того же зелёного, что и заголовки экрана.
    """

    def __init__(
        self,
        master,
        textvariable: tk.StringVar,
        width: int = 400,
        height: int = 40,
        font_size: int = 11,
        bg: str = WHITE,
        text_color: str = GREEN,
        **kwargs,
    ):
        super().__init__(master, bg=bg, **kwargs)
        self._bw = width
        self._bh = height
        self.canvas = tk.Canvas(
            self, width=width, height=height, bg=bg, highlightthickness=0, bd=0
        )
        self.canvas.pack()
        round_rect(
            self.canvas,
            1,
            1,
            width - 1,
            height - 1,
            RADIUS_FIELD,
            fill=GREEN_SOFT,
            outline="",
        )
        self.entry = tk.Entry(
            self.canvas,
            textvariable=textvariable,
            bd=0,
            relief="flat",
            bg=GREEN_SOFT,
            fg=text_color,
            font=font(font_size),
            insertbackground=GREEN,
            highlightthickness=0,
        )
        self.canvas.create_window(
            16,
            height / 2,
            window=self.entry,
            anchor="w",
            width=width - 32,
            height=height - 14,
        )

    def set_focus_ring(self, on: bool, color: str = GREEN) -> None:
        self.canvas.delete("ring")
        if on:
            round_rect(
                self.canvas,
                1,
                1,
                self._bw - 1,
                self._bh - 1,
                RADIUS_FIELD,
                fill="",
                outline=color,
                width=2,
                tags="ring",
            )


class RadioDot(tk.Canvas):
    """Переключатель с растровой точкой (гладкие края вместо пиксельных)."""

    def __init__(
        self,
        master,
        label: str,
        variable: tk.StringVar,
        value: str,
        bg: str = WHITE,
        font_size: int = 11,
        command=None,
    ):
        self._label = label
        self._var = variable
        self._value = value
        self._font_size = font_size
        self._command = command
        super().__init__(
            master, width=430, height=32, bg=bg, highlightthickness=0, bd=0
        )
        self.bind("<Button-1>", self._select)
        self.configure(cursor="hand2")
        self._var.trace_add("write", lambda *_: self._draw())
        self._draw()

    def _select(self, _e=None):
        self._var.set(self._value)
        if self._command:
            self._command()

    def _draw(self):
        self.delete("all")
        selected = self._var.get() == self._value
        size = 22
        cy = 16
        photo = toggle_image("radio", selected, size)
        if photo is not None:
            self.create_image(2, cy, image=photo, anchor="w")
        else:
            self.create_oval(
                2, cy - 10, 22, cy + 10, fill=WHITE, outline="#8FA88F", width=2
            )
        self.create_text(
            size + 12,
            cy,
            text=self._label,
            anchor="w",
            fill=GREEN,
            font=font(self._font_size),
        )


class CheckSquare(tk.Canvas):
    """Флажок с растровой галочкой. Управляет BooleanVar."""

    def __init__(
        self,
        master,
        label: str,
        variable: tk.BooleanVar,
        bg: str = WHITE,
        font_size: int = 11,
    ):
        self._label = label
        self._var = variable
        self._font_size = font_size
        super().__init__(
            master, width=430, height=32, bg=bg, highlightthickness=0, bd=0
        )
        self.bind("<Button-1>", self._toggle)
        self.configure(cursor="hand2")
        self._draw()

    def _toggle(self, _e=None):
        self._var.set(not self._var.get())
        self._draw()

    def _draw(self):
        self.delete("all")
        size = 24
        cy = 16
        photo = toggle_image("check", bool(self._var.get()), size)
        if photo is not None:
            self.create_image(2, cy, image=photo, anchor="w")
        else:
            self.create_rectangle(
                2, cy - 10, 22, cy + 10, fill=WHITE, outline="#8FA88F", width=2
            )
        self.create_text(
            size + 12,
            cy,
            text=self._label,
            anchor="w",
            fill=INK,
            font=font(self._font_size),
        )


class DropZone(tk.Canvas):
    """Зона перетаскивания: скруглённая зелёная подложка и пунктирная рамка."""

    def __init__(
        self,
        master,
        width: int = 440,
        height: int = 268,
        bg: str = WHITE,
        onclick: Callable[[], None] | None = None,
    ):
        super().__init__(
            master, width=width, height=height, bg=bg, highlightthickness=0, bd=0
        )
        self._bw = width
        self._bh = height
        self._onclick = onclick
        if onclick is not None:
            self.bind("<Button-1>", lambda _e: onclick())
            self.configure(cursor="hand2")
        self._draw()

    def _draw(self):
        self.delete("all")
        inset = 2
        round_rect(
            self,
            inset,
            inset,
            self._bw - inset,
            self._bh - inset,
            RADIUS_ZONE_BACK,
            fill=GREEN_SOFT,
            outline="",
        )
        dashed_round_rect(
            self,
            inset,
            inset,
            self._bw - inset,
            self._bh - inset,
            RADIUS_ZONE,
            color=GREEN,
            width=2,
            dash=(22, 12),
        )

    def bind_child(self, widget: tk.Widget) -> None:
        if self._onclick is not None:
            widget.bind("<Button-1>", lambda _e: self._onclick())
