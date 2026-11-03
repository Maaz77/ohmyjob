"""Tracker: the xlsx job tracker every script reads and writes."""
import pytest
from openpyxl import Workbook, load_workbook

import tracker as T
from tracker import STATUSES, Tracker, TrackerError

LINKEDIN = "https://www.linkedin.com/jobs/view/4399000001"
INDEED = "https://de.indeed.com/viewjob?jk=4be1a09c37d5f268"


@pytest.fixture
def path(workspace):
    return workspace / "Job_Tracker.xlsx"


@pytest.fixture
def tr(path):
    return Tracker.create(path)


def workbook_with(path, header, *rows, sheet="Jobs"):
    wb = Workbook()
    wb.active.title = sheet
    wb.active.append(header)
    for row in rows:
        wb.active.append(row)
    wb.save(path)


def test_create_writes_the_header_row_bold_frozen_with_a_filter(path):
    Tracker.create(path)
    sheet = load_workbook(path)["Jobs"]
    assert [c.value for c in sheet[1]] == [
        "Job URL", "Status", "Company", "Job Title", "Date Found", "Location", "Source", "Match Score", "Notes",
    ]
    assert all(c.font.bold for c in sheet[1])
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref == "A1:I1"
    assert Tracker(path).rows() == []


def test_module_level_create_is_the_same_function(path):
    assert isinstance(T.create(path), Tracker)


def test_create_never_overwrites(path, tr):
    tr.add_row({"Job URL": LINKEDIN})
    tr.save()
    with pytest.raises(TrackerError, match="already exists"):
        Tracker.create(path)
    assert len(Tracker(path).rows()) == 1


def test_round_trip_keeps_values_index_and_key(path, tr):
    first = tr.add_row({"Job URL": LINKEDIN, "Company": "Lumenfield Logistics", "Job Title": "Operations Analyst",
                        "Status": "Found", "Date Found": "2026-10-20 09:00", "Source": "linkedin", "Match Score": 7.5})
    second = tr.add_row({"Job URL": INDEED, "Company": "Quillon Systems", "Status": "Found"})
    assert (first, second) == (1, 2)
    tr.save()
    rows = Tracker(path).rows()
    assert [r["_index"] for r in rows] == [1, 2]
    assert [r["_key"] for r in rows] == ["4399000001", "969845171025937"]
    assert rows[0]["Company"] == "Lumenfield Logistics"
    assert rows[0]["Match Score"] == 7.5
    assert rows[0]["Date Found"] == "2026-10-20 09:00"
    assert rows[1]["Job Title"] is None


def test_rows_for_key(tr):
    tr.add_row({"Job URL": LINKEDIN})
    tr.add_row({"Job URL": INDEED})
    tr.add_row({"Job URL": LINKEDIN + "/?trk=x"})
    assert [r["_index"] for r in tr.rows_for_key("4399000001")] == [1, 3]
    assert tr.rows_for_key("123") == []


def test_set_status_appends_the_note_on_a_new_line(path, tr):
    index = tr.add_row({"Job URL": LINKEDIN, "Status": "Found"})
    tr.set_status(index, "Matched", None)
    tr.set_status(index, "Resume Failed", "Resume Failed 2026-10-21: overflow")
    tr.set_status(index, "Needs Attention", "second note")
    tr.save()
    row = Tracker(path).rows()[0]
    assert row["Status"] == "Needs Attention"
    assert row["Notes"] == "Resume Failed 2026-10-21: overflow\nsecond note"


def test_set_status_refuses_an_unknown_status(tr):
    index = tr.add_row({"Job URL": LINKEDIN})
    with pytest.raises(TrackerError, match="Unknown status 'Resume built'"):
        tr.set_status(index, "Resume built", None)
    with pytest.raises(TrackerError, match="Unknown status"):
        tr.add_row({"Job URL": INDEED, "Status": "found"})
    assert tr.rows()[0]["Status"] is None


def test_every_status_is_accepted(tr):
    index = tr.add_row({"Job URL": LINKEDIN})
    for status in STATUSES:
        tr.set_status(index, status, None)


def test_set_cell(path, tr):
    index = tr.add_row({"Job URL": LINKEDIN, "Match Score": 4})
    tr.set_cell(index, "Match Score", 8)
    tr.set_cell(index, "Location", "Berlin, Germany")
    tr.set_cell(index, "Match Score", None)
    tr.save()
    row = Tracker(path).rows()[0]
    assert row["Location"] == "Berlin, Germany"
    assert row["Match Score"] is None


def test_set_cell_refuses_the_header_a_missing_row_and_a_missing_column(tr):
    index = tr.add_row({"Job URL": LINKEDIN})
    with pytest.raises(TrackerError, match="does not exist"):
        tr.set_cell(0, "Status", "Found")
    with pytest.raises(TrackerError, match="does not exist"):
        tr.set_cell(index + 1, "Status", "Found")
    with pytest.raises(TrackerError, match="Unknown column 'Salary'"):
        tr.set_cell(index, "Salary", "1")


def test_add_row_skips_empty_values_and_unknown_headers(tr):
    index = tr.add_row({"Job URL": LINKEDIN, "Company": "", "Job Title": None, "Not A Column": "x"})
    assert tr.rows()[0] == {
        "Job URL": LINKEDIN, "Status": None, "Company": None, "Job Title": None, "Date Found": None,
        "Location": None, "Source": None, "Match Score": None, "Notes": None, "_index": index, "_key": "4399000001",
    }
    with pytest.raises(TrackerError, match="no value"):
        tr.add_row({"Company": "", "Not A Column": "x"})


def test_add_row_reuses_blank_rows_at_the_end(path, tr):
    tr.add_row({"Job URL": LINKEDIN})
    tr.add_row({"Job URL": INDEED})
    tr.save()
    sheet = load_workbook(path)
    for cell in sheet["Jobs"][3]:  # the user cleared the second job in Excel
        cell.value = None
    sheet.save(path)
    tr = Tracker(path)
    assert [r["_index"] for r in tr.rows()] == [1]
    assert tr.add_row({"Job URL": INDEED}) == 2


def test_blank_rows_in_the_middle_are_skipped_and_keep_their_index(path):
    workbook_with(path, T.HEADERS, [LINKEDIN], [None], [INDEED])
    assert [r["_index"] for r in Tracker(path).rows()] == [1, 3]


def test_columns_are_found_by_name_and_other_columns_survive(path):
    header = ["Notes", "Extra", *[h for h in T.HEADERS if h != "Notes"]]
    workbook_with(path, header, ["old note", "mine", *[LINKEDIN if h == "Job URL" else None for h in header[2:]]])
    tr = Tracker(path)
    assert tr.rows()[0]["Notes"] == "old note"
    tr.set_status(1, "Matched", "scored")
    tr.save()
    sheet = load_workbook(path)["Jobs"]
    assert [c.value for c in sheet[2]][:2] == ["old note\nscored", "mine"]


def test_a_missing_header_is_refused_with_the_headers_it_found(path):
    workbook_with(path, [h for h in T.HEADERS if h != "Source"])
    with pytest.raises(TrackerError, match=r"Header 'Source' not found. Got: \['Job URL'"):
        Tracker(path)


def test_a_missing_sheet_or_file_is_refused(path, workspace):
    workbook_with(path, T.HEADERS, sheet="Sheet1")
    with pytest.raises(TrackerError, match="Sheet 'Jobs' not found"):
        Tracker(path)
    with pytest.raises(TrackerError, match="not found"):
        Tracker(workspace / "missing.xlsx")
    path.write_text("not a workbook")
    with pytest.raises(TrackerError, match="Could not open tracker"):
        Tracker(path)


def test_a_job_url_that_is_not_text_still_gets_a_key(path):
    workbook_with(path, T.HEADERS, [12345])
    assert Tracker(path).rows()[0]["_key"] == "12345"


def test_save_replaces_the_file_and_leaves_no_temp_file(path, tr):
    tr.add_row({"Job URL": LINKEDIN})
    tr.save()
    tr.add_row({"Job URL": INDEED})
    tr.save()
    assert sorted(p.name for p in path.parent.iterdir() if "Tracker" in p.name) == ["Job_Tracker.xlsx"]
    assert len(Tracker(path).rows()) == 2


def test_a_failed_save_keeps_the_old_file_and_removes_the_temp_file(path, tr, monkeypatch):
    tr.add_row({"Job URL": LINKEDIN})
    tr.save()
    before = path.read_bytes()
    tr.add_row({"Job URL": INDEED})

    def broken_save(self, filename):
        open(filename, "wb").write(b"half a workbook")
        raise OSError("disk full")

    monkeypatch.setattr(Workbook, "save", broken_save)
    with pytest.raises(OSError, match="disk full"):
        tr.save()
    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir() if "Tracker" in p.name] == ["Job_Tracker.xlsx"]


@pytest.mark.parametrize("lock_name", ["~$Job_Tracker.xlsx", "~$b_Tracker.xlsx", ".~lock.Job_Tracker.xlsx#"])
def test_save_refuses_while_excel_or_libreoffice_has_the_file_open(path, tr, lock_name):
    tr.add_row({"Job URL": LINKEDIN})
    tr.save()
    before = path.read_bytes()
    lock = path.parent / lock_name
    lock.write_bytes(b"owner")
    tr.add_row({"Job URL": INDEED})
    with pytest.raises(TrackerError, match="Close the tracker in Excel/LibreOffice first") as exc:
        tr.save()
    assert lock_name in str(exc.value)
    assert path.read_bytes() == before
    lock.unlink()
    tr.save()  # the rows added while it was locked are still in memory
    assert len(Tracker(path).rows()) == 2


# --------------------------------------------------------------------------- cell text


@pytest.mark.parametrize("text", ["=1+1", "=HYPERLINK(\"http://x\",\"y\")", "+SUM(A1)", "@x"])
def test_text_that_looks_like_a_formula_is_stored_as_text(path, tr, text):
    index = tr.add_row({"Job URL": LINKEDIN, "Job Title": text})
    tr.set_cell(index, "Company", text)
    tr.set_status(index, "Found", text)
    tr.save()
    cells = {c.column_letter: c for c in load_workbook(path)["Jobs"][2]}
    for letter in ("C", "D", "I"):
        assert cells[letter].data_type == "s", letter
        assert cells[letter].value == text
    assert Tracker(path).rows()[0]["Job Title"] == text


def test_control_characters_are_dropped_and_long_text_is_clipped(path, tr):
    index = tr.add_row({"Job URL": LINKEDIN, "Job Title": "Ops\x1b[31m Analyst\x00\x08", "Location": "x" * 40000})
    tr.set_status(index, "Found", "note\x1b")
    tr.save()  # an IllegalCharacterError here would not be a TrackerError
    row = Tracker(path).rows()[0]
    assert row["Job Title"] == "Ops[31m Analyst"
    assert len(row["Location"]) == T.MAX_CELL_CHARS
    assert row["Notes"] == "note"


def test_the_notes_keep_their_newest_lines_when_they_outgrow_a_cell(path, tr):
    index = tr.add_row({"Job URL": LINKEDIN, "Notes": "o" * T.MAX_CELL_CHARS})
    tr.set_status(index, "Found", "newest line")
    assert tr.rows()[0]["Notes"].endswith("\nnewest line")
    assert len(tr.rows()[0]["Notes"]) == T.MAX_CELL_CHARS


# --------------------------------------------------------------------------- stale workbook


def test_a_save_refuses_when_the_file_changed_after_the_load(path, tr):
    index = tr.add_row({"Job URL": LINKEDIN, "Status": "Found"})
    tr.save()
    stale = Tracker(path)  # a scrape that loaded the tracker before /prep-jobs saved it
    fresh = Tracker(path)
    fresh.set_status(index, "Matched", None)
    fresh.save()
    stale.add_row({"Job URL": INDEED})
    before = path.read_bytes()
    with pytest.raises(TrackerError, match="changed on disk since this run loaded it") as exc:
        stale.save()
    assert "Nothing was saved" in str(exc.value)
    assert path.read_bytes() == before
    assert Tracker(path).rows()[0]["Status"] == "Matched"


def test_a_tracker_can_be_saved_again_after_its_own_save(path, tr):
    tr.add_row({"Job URL": LINKEDIN})
    tr.save()
    tr.add_row({"Job URL": INDEED})
    tr.save()
    assert len(Tracker(path).rows()) == 2


def test_a_save_refuses_when_the_file_was_deleted(path, tr):
    tr.add_row({"Job URL": LINKEDIN})
    path.unlink()
    with pytest.raises(TrackerError, match="changed on disk"):
        tr.save()
