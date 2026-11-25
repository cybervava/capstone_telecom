"""Looks and small cell helpers for the ROI workbook.

The colour convention is the one financial models use, and the `Read me` sheet repeats it:
blue text is a typed input, black a formula, green a value brought from another sheet, and a
yellow fill marks an assumption or a figure nobody has established yet.
"""

from __future__ import annotations

import textwrap

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.indexed_list import IndexedList
from openpyxl.worksheet.worksheet import Worksheet

FONT_NAME = "Arial"
FONT_SIZE = 10
# Arial 10 sets about 1.15 characters of running text in one unit of column width, and a line of
# it needs 13 points of row height. Used to give rows of wrapped text a height that fits.
CHARS_PER_WIDTH_UNIT = 1.15
BOLD_CHARS_PER_WIDTH_UNIT = 1.05
LINE_POINTS = 13.0

# The ink and the tints of the submission's PDFs (presentation/src/pdf/base.css).
INK = "12233A"
INK_SOFT = "43556B"
LINE = "CCD6DF"
GROUND = "EDF1F4"
ACCENT_SOFT = "D7EEF0"
WARN_SOFT = "FBE3E0"
WHITE = "FFFFFF"

# The model convention.
BLUE = "0000FF"
BLACK = "000000"
GREEN = "008000"
YELLOW = "FFFF00"

# Number formats. Money shows its currency; a negative is in brackets; a zero is a dash unless
# the zero is itself the statement (a count of nought, a benefit held at nought).
USD = '"$"#,##0;("$"#,##0);"-"'
USD_ZERO = '"$"#,##0;("$"#,##0);"$0"'
USD_2 = '"$"#,##0.00;("$"#,##0.00);"-"'
USD_M = '"$"0.00,,"m"'
USD_4 = '"$"0.0000;("$"0.0000);"-"'
COUNT = '#,##0;(#,##0);"-"'
COUNT_ZERO = "#,##0"
PCT_0 = "0%"
PCT_1 = "0.0%"
PCT_2 = "0.00%"
RATE_2 = "0.00"
# A formula that returns words keeps the General format: a cell formatted as text would show the
# formula itself once somebody edits it.
TEXT = "General"

HAIR = Side(style="hair", color=LINE)
THIN = Side(style="thin", color=INK)

FONT_COLOUR = {
    "input": BLUE,
    "calc": BLACK,
    "link": GREEN,
    "label": INK,
    "note": INK_SOFT,
}


def font(
    colour: str = INK, *, bold: bool = False, size: float = FONT_SIZE, italic: bool = False
) -> Font:
    return Font(name=FONT_NAME, size=size, bold=bold, italic=italic, color=colour)


def new_workbook() -> Workbook:
    """An empty workbook whose default font is the model's, so that an untouched cell matches."""
    wb = Workbook()
    wb._fonts = IndexedList([Font(name=FONT_NAME, size=FONT_SIZE, family=2)])
    wb.remove(wb.active)
    return wb


def fill(colour: str) -> PatternFill:
    return PatternFill("solid", start_color=colour, end_color=colour)


def put(
    ws: Worksheet,
    ref: str,
    value: object,
    kind: str = "label",
    *,
    fmt: str | None = None,
    bold: bool = False,
    wrap: bool = False,
    align: str | None = None,
    indent: int = 0,
    shade: str | None = None,
    rule: bool = True,
) -> None:
    """Write one cell in the style of its kind: input, calc, link, label or note."""
    cell = ws[ref]
    cell.value = value
    cell.font = font(FONT_COLOUR[kind], bold=bold)
    is_figure = kind in ("input", "calc", "link")
    side = align or ("right" if is_figure else "left")
    cell.alignment = Alignment(
        horizontal=side, vertical="top", wrap_text=wrap, indent=1 if side == "right" else indent
    )
    if fmt:
        cell.number_format = fmt
    if shade:
        cell.fill = fill(shade)
    if rule:
        cell.border = Border(bottom=HAIR)


def title(ws: Worksheet, text: str, lines: tuple[str, ...] = ()) -> int:
    """The sheet's heading in A1 and its lead lines under it. Returns the next free row."""
    ws["A1"].value = text
    ws["A1"].font = font(INK, bold=True, size=14)
    ws.row_dimensions[1].height = 22
    row = 2
    for line in lines:
        ws.cell(row=row, column=1, value=line).font = font(INK_SOFT)
        row += 1
    return row + 1


def table_head(ws: Worksheet, row: int, labels: tuple[str, ...], *, first_col: int = 1) -> None:
    for offset, label in enumerate(labels):
        cell = ws.cell(row=row, column=first_col + offset, value=label)
        cell.font = font(WHITE, bold=True)
        cell.fill = fill(INK)
        cell.alignment = Alignment(
            horizontal="left" if offset == 0 else "center", vertical="center", wrap_text=True
        )
    ws.row_dimensions[row].height = 30


def section(ws: Worksheet, row: int, text: str, *, last_col: int) -> None:
    """A band across the table that names the group of rows under it."""
    for col in range(1, last_col + 1):
        cell = ws.cell(row=row, column=col)
        cell.fill = fill(GROUND)
        cell.border = Border(top=Side(style="thin", color=LINE))
    ws.cell(row=row, column=1, value=text).font = font(INK, bold=True)


def emphasise(ws: Worksheet, row: int, *, last_col: int) -> None:
    """Mark a result row: bold, tinted, ruled above."""
    for col in range(1, last_col + 1):
        cell = ws.cell(row=row, column=col)
        cell.font = font(cell.font.color.rgb[-6:] if cell.font.color else INK, bold=True)
        cell.fill = fill(ACCENT_SOFT)
        cell.border = Border(top=THIN, bottom=HAIR)


def widths(ws: Worksheet, sizes: dict[str, float]) -> None:
    for letter, size in sizes.items():
        ws.column_dimensions[letter].width = size


def fit_rows(ws: Worksheet) -> None:
    """Give each row of wrapped text the height its longest cell needs.

    Excel sizes such rows by itself on opening; LibreOffice and file previews do not, and the
    text of one row then runs into the next. An explicit height reads the same everywhere.
    """
    for row in ws.iter_rows():
        lines = max((_lines(ws, cell) for cell in row), default=1)
        if lines > 1:
            dimension = ws.row_dimensions[row[0].row]
            dimension.height = max(dimension.height or 0, LINE_POINTS * lines + 3)


def _lines(ws: Worksheet, cell) -> int:
    text = cell.value
    if not (cell.alignment.wrap_text and isinstance(text, str)) or text.startswith("="):
        return 1
    per_unit = BOLD_CHARS_PER_WIDTH_UNIT if cell.font.bold else CHARS_PER_WIDTH_UNIT
    width = ws.column_dimensions[get_column_letter(cell.column)].width
    return len(textwrap.wrap(text, max(6, int((width - 1.5) * per_unit)))) or 1


def page(
    ws: Worksheet, *, tab: str = INK, last_column: str, repeat_rows: str | None = None
) -> None:
    """Screen and print set-up shared by every sheet: no gridlines, A4 landscape, one page wide.

    The print area runs to `last_column`, so that a sentence which runs across empty cells is
    printed whole."""
    ws.sheet_view.showGridLines = False
    ws.print_area = f"A1:{last_column}{ws.max_row}"
    ws.sheet_properties.tabColor = tab
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_margins.left = ws.page_margins.right = 0.5
    ws.page_margins.top = ws.page_margins.bottom = 0.6
    ws.oddFooter.left.text = "&A"
    ws.oddFooter.center.text = (
        "G3 Telco ROI model. Fictional client, synthetic data. A scenario, not a forecast."
    )
    ws.oddFooter.right.text = "&P / &N"
    for part in (ws.oddFooter.left, ws.oddFooter.center, ws.oddFooter.right):
        part.size = 8
        part.font = FONT_NAME
    if repeat_rows:
        ws.print_title_rows = repeat_rows
