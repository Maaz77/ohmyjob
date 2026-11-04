"""The resume PDF to upload is any PDF next to job.md (several: the newest by modification time); no name is required."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import aa_lib as L
import test_aa as T
from test_aa import C, W


class TestAnyPdf(T.Base):
    jobs = (T.INDEED_JOB,)

    def folder(self) -> Path:
        return self.root / "Applications" / T.INDEED_JOB

    def test_job_pdf_takes_the_newest_pdf_whatever_its_name(self):
        folder = self.folder()
        for pdf in folder.glob("*.pdf"):
            pdf.unlink()
        assert L.job_pdf(folder) is None
        old, new = folder / "my old cv.pdf", folder / "ZZZ.PDF"
        old.write_bytes(b"%PDF-1.4 old")
        new.write_bytes(b"%PDF-1.4 new")
        os.utime(old, (1_000_000, 1_000_000))
        os.utime(new, (2_000_000, 2_000_000))
        (folder / "notes.txt").write_text("not a pdf")
        assert L.job_pdf(folder) == new
        os.utime(old, (3_000_000, 3_000_000))  # touched later: now the newest
        assert L.job_pdf(folder) == old

    def test_the_brief_and_the_upload_guard_use_that_pdf(self):
        folder = self.folder()
        for pdf in folder.glob("*.pdf"):
            pdf.unlink()
        (folder / "Resume final.pdf").write_bytes(b"%PDF-1.4 x")
        self.aa_json("start")
        [(name, tab, brief)] = self.assign(101)
        expected = str((folder / "Resume final.pdf").resolve())
        assert f"Resume PDF to upload (exact path): {expected}" in Path(brief).read_text(encoding="utf-8")
        self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://example.com/jobs/1"}, W, "A1"))
        self.page(tab, "constructed_form_page.txt")
        ok = self.guard(C + "file_upload", {"tabId": tab, "ref": "ref_50", "paths": [expected]}, W, "A1")
        self.assertNotEqual(ok.returncode, 2, ok.stderr)
        self.assertDenied(self.guard(C + "file_upload", {"tabId": tab, "ref": "ref_50", "paths": [str(folder / "other.pdf")]}, W, "A1"),
                          "Upload only this one file")

    def test_a_folder_without_any_pdf_is_skipped_with_a_plain_reason(self):
        folder = self.folder()
        backup = folder.parent / "backup"
        shutil.copytree(folder, backup)
        try:
            for pdf in folder.glob("*.pdf"):
                pdf.unlink()
            start = self.aa_json("start")
            assert start["count"] == 0 and "no PDF next to job.md" in str(start)
        finally:
            shutil.rmtree(folder)
            shutil.move(str(backup), str(folder))
