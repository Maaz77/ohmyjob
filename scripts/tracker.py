"""Read and update Job_Tracker.xlsx (sheet "Jobs").

Columns are found by header name, and a row is matched by the job_key of its "Job URL".
A row's `_index` counts the header row as 0: the first job is 1, and its sheet row is `_index + 1`.
Every script uses this API only. save() is atomic; it refuses while Excel or LibreOffice holds the file open and when
the file changed on disk since it was loaded (a stale workbook would revert another run's statuses).
Text written to a cell is always stored as text (scraped "=..." is no formula), without control characters, clipped.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from job_key import job_key

MAX_CELL_CHARS = 32767  # Excel's limit for one cell
SHEET_NAME = "Jobs"
HEADERS = ["Job URL", "Status", "Company", "Job Title", "Date Found", "Location", "Source", "Match Score", "Notes"]
STATUSES = (
    "Found", "Matched", "Skipped-LowFit", "Resume Built", "Resume Failed",
    "Pending Review", "Needs Attention", "Submitted", "Deleted",
)


class TrackerError(Exception):
    pass


def _clean(value: Any) -> Any:
    """Text without the control characters xlsx cannot hold, clipped to one cell; other values unchanged."""
    if isinstance(value, str):
        return ILLEGAL_CHARACTERS_RE.sub("", value)[:MAX_CELL_CHARS]
    return value


def _put(cell, value: Any) -> None:
    """Write a value; text stays text even when it starts with "=" (a scraped title must never become a formula)."""
    value = _clean(value)
    cell.value = value
    if isinstance(value, str):
        cell.data_type = "s"


def _save_atomically(workbook: Workbook, path: Path) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        workbook.save(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class Tracker:
    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.exists():
            raise TrackerError(f"Tracker not found: {path}")
        try:
            self._loaded_at = self.path.stat().st_mtime_ns
            self.workbook = load_workbook(self.path)
        except Exception as exc:  # noqa: BLE001
            raise TrackerError(f"Could not open tracker {path}: {exc}") from exc
        if SHEET_NAME not in self.workbook.sheetnames:
            raise TrackerError(f"Sheet '{SHEET_NAME}' not found")
        self.sheet = self.workbook[SHEET_NAME]
        header = [str(c.value).strip() if c.value is not None else "" for c in self.sheet[1]]
        self.col: dict[str, int] = {}  # header name -> 0-based column
        for name in HEADERS:
            if name not in header:
                raise TrackerError(f"Header '{name}' not found. Got: {header}")
            self.col[name] = header.index(name)

    @classmethod
    def create(cls, path: Path) -> "Tracker":
        """A new tracker file with only the header row (bold, frozen, with a filter). Never overwrites a file."""
        path = Path(path)
        if path.exists():
            raise TrackerError(f"{path} already exists")
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = SHEET_NAME
        sheet.append(HEADERS)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}1"
        path.parent.mkdir(parents=True, exist_ok=True)
        _save_atomically(workbook, path)
        return cls(path)

    def rows(self) -> list[dict]:
        out = []
        for i, cells in enumerate(self.sheet.iter_rows(values_only=True)):
            if i == 0 or all(v in (None, "") for v in cells):
                continue
            item = {name: cells[idx] if idx < len(cells) else None for name, idx in self.col.items()}
            item["_index"] = i
            item["_key"] = job_key(str(item["Job URL"]) if item["Job URL"] is not None else "")
            out.append(item)
        return out

    def rows_for_key(self, key: str) -> list[dict]:
        return [r for r in self.rows() if r["_key"] == key]

    def set_cell(self, index: int, column: str, value: Any) -> None:
        if column not in self.col:
            raise TrackerError(f"Unknown column '{column}'. Columns: {HEADERS}")
        if not 1 <= index < self.sheet.max_row:
            raise TrackerError(f"Row {index} does not exist")
        _put(self.sheet.cell(row=index + 1, column=self.col[column] + 1), value)

    def set_status(self, index: int, status: str, note_line: Optional[str]) -> None:
        self._check_status(status)
        self.set_cell(index, "Status", status)
        if note_line:
            current = self.sheet.cell(row=index + 1, column=self.col["Notes"] + 1).value or ""
            combined = f"{current}\n{note_line}" if current else note_line
            self.set_cell(index, "Notes", ILLEGAL_CHARACTERS_RE.sub("", combined)[-MAX_CELL_CHARS:])  # a long history loses its oldest lines

    def add_row(self, values: dict) -> int:
        """Append a row from {header: value} (unknown headers, None and "" are skipped); returns its `_index`."""
        known = {name: v for name, v in values.items() if name in self.col and v is not None and v != ""}
        if not known:
            raise TrackerError("add_row got no value for any column")
        if "Status" in known:
            self._check_status(known["Status"])
        last = self.sheet.max_row
        while last > 1 and all(c.value in (None, "") for c in self.sheet[last]):
            last -= 1  # blank rows at the end (cleared in Excel) are reused
        for name, value in known.items():
            _put(self.sheet.cell(row=last + 1, column=self.col[name] + 1), value)
        return last

    def save(self) -> None:
        lock = self._lock_file()
        if lock is not None:
            raise TrackerError(
                f"Close the tracker in Excel/LibreOffice first ({lock.name} exists). Nothing was saved."
            )
        try:
            changed = self.path.stat().st_mtime_ns != self._loaded_at
        except OSError:
            changed = True
        if changed:
            raise TrackerError(
                f"{self.path.name} changed on disk since this run loaded it (another run or Excel saved it). "
                "Nothing was saved; run the command again."
            )
        _save_atomically(self.workbook, self.path)
        self._loaded_at = self.path.stat().st_mtime_ns

    def _lock_file(self) -> Optional[Path]:
        """The file Excel (~$ + the name, sometimes without its first letters) or LibreOffice (.~lock.<name>#) keeps while the tracker is open."""
        name = self.path.name
        for lock_name in [f"~${name[k:]}" for k in range(3)] + [f".~lock.{name}#"]:
            if (self.path.parent / lock_name).exists():
                return self.path.parent / lock_name
        return None

    @staticmethod
    def _check_status(status: str) -> None:
        if status not in STATUSES:
            raise TrackerError(f"Unknown status '{status}'. Statuses: {', '.join(STATUSES)}")


create = Tracker.create
