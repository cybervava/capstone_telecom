"""Store the results of the formulas inside the workbook.

openpyxl writes a formula without its result. Excel and LibreOffice calculate on opening, but a
file preview does not, and neither does Excel's Protected View, which is how a workbook
downloaded from the LMS first opens: every figure would be empty until the reader enables
editing. So LibreOffice calculates a copy, and only the values it found are written into the
workbook that openpyxl built. Nothing else in the file changes.

LibreOffice is taken from the PATH (`soffice`) when it is installed, and otherwise from the
container the presentation uses (`docker build -t skylink-presentation-lo:1
presentation/src/libreoffice`).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from openpyxl import load_workbook

IMAGE = "skylink-presentation-lo:1"
TIMEOUT_SECONDS = 180
MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PACKAGE_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
# A formula cell exactly as openpyxl writes it: reference, optional style, formula, empty value.
FORMULA_CELL = re.compile(r'<c r="([A-Z]+[0-9]+)"((?: s="[0-9]+")?)><f>([^<]*)</f><v ?/></c>')

Value = bool | int | float | str


class ResultsError(RuntimeError):
    """The results could not be calculated or stored; the workbook is left as it was built."""


def store_results(workbook: Path) -> int:
    """Calculate `workbook` with LibreOffice and write each formula's result beside it.

    Returns the number of formulas given a result."""
    with tempfile.TemporaryDirectory(prefix="roi-results-") as folder:
        work = Path(folder)
        values = _read_values(_calculate_copy(workbook, work))
        rebuilt = work / "with-results.xlsx"
        stored = _rewrite(workbook, rebuilt, values)
        shutil.copyfile(rebuilt, workbook)
    return stored


def _calculate_copy(workbook: Path, work: Path) -> Path:
    """Have LibreOffice open a copy, calculate it and save it again. Returns the saved copy."""
    (work / "out").mkdir()
    shutil.copyfile(workbook, work / "in.xlsx")
    convert = ["--headless", "--convert-to", "xlsx", "--outdir"]
    local = shutil.which("soffice") or shutil.which("libreoffice")
    if local:
        profile = (work / "profile").as_uri()
        command = [
            local,
            f"-env:UserInstallation={profile}",
            *convert,
            str(work / "out"),
            str(work / "in.xlsx"),
        ]
    elif shutil.which("docker"):
        user = ["-u", f"{os.getuid()}:{os.getgid()}"] if hasattr(os, "getuid") else []
        command = [
            "docker",
            "run",
            "--rm",
            *user,
            "-e",
            "HOME=/tmp",
            "-v",
            f"{work}:/work",
            IMAGE,
            "-env:UserInstallation=file:///tmp/lo-profile",
            *convert,
            "/work/out",
            "/work/in.xlsx",
        ]
    else:
        raise ResultsError("neither LibreOffice (soffice) nor Docker is on the PATH")
    try:
        # The command is a fixed list built above: LibreOffice or Docker, and paths of our own.
        done = subprocess.run(  # noqa: S603
            command, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False
        )
    except subprocess.TimeoutExpired as slow:
        raise ResultsError(f"LibreOffice did not finish within {TIMEOUT_SECONDS} s") from slow
    calculated = work / "out" / "in.xlsx"
    if done.returncode != 0 or not calculated.is_file():
        detail = (done.stderr or done.stdout).strip()[-400:]
        raise ResultsError(f"LibreOffice produced no calculated copy: {detail}")
    return calculated


def _read_values(calculated: Path) -> dict[tuple[str, str], Value]:
    book = load_workbook(calculated, data_only=True)
    return {
        (sheet.title, cell.coordinate): cell.value
        for sheet in book.worksheets
        for row in sheet.iter_rows()
        for cell in row
        if cell.value is not None
    }


def _sheet_parts(archive: zipfile.ZipFile) -> dict[str, str]:
    """Map each worksheet's part in the package to its tab name.

    The package is the one this build has just written, so its XML is our own."""
    rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))  # noqa: S314
    book = ElementTree.fromstring(archive.read("xl/workbook.xml"))  # noqa: S314
    targets = {
        rel.get("Id"): rel.get("Target").lstrip("/")
        for rel in rels.iter(f"{PACKAGE_REL}Relationship")
    }
    sheets = book.iter(f"{MAIN}sheet")
    parts = {}
    for sheet in sheets:
        target = targets[sheet.get(f"{REL}id")]
        parts[target if target.startswith("xl/") else f"xl/{target}"] = sheet.get("name")
    return parts


def _cell_with_result(
    match: re.Match[str], sheet: str, values: dict[tuple[str, str], Value]
) -> str:
    ref, style, formula = match.groups()
    value = values.get((sheet, ref))
    if value is None:
        raise ResultsError(f"{sheet}!{ref} has no result after calculation")
    if isinstance(value, str) and value.startswith("#"):
        raise ResultsError(f"{sheet}!{ref} is the error {value}")
    if isinstance(value, bool):
        return f'<c r="{ref}"{style} t="b"><f>{formula}</f><v>{int(value)}</v></c>'
    if isinstance(value, str):
        return f'<c r="{ref}"{style} t="str"><f>{formula}</f><v>{escape(value)}</v></c>'
    return f'<c r="{ref}"{style}><f>{formula}</f><v>{value!r}</v></c>'


def _rewrite(workbook: Path, rebuilt: Path, values: dict[tuple[str, str], Value]) -> int:
    """Copy the package part by part, filling in the result of every formula cell."""
    stored = 0
    with (
        zipfile.ZipFile(workbook) as source,
        zipfile.ZipFile(rebuilt, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        parts = _sheet_parts(source)
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename in parts:
                sheet = parts[item.filename]
                text = data.decode("utf-8")
                text, count = FORMULA_CELL.subn(
                    lambda m, s=sheet: _cell_with_result(m, s, values), text
                )
                missed = len(re.findall(r"<f[ >]", text)) - count
                if missed:
                    raise ResultsError(f"{sheet}: {missed} formula cells were not recognised")
                stored += count
                data = text.encode("utf-8")
            target.writestr(item, data)
    return stored
