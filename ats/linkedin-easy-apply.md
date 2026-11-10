# LinkedIn Easy Apply
hosts: linkedin.com, *.linkedin.com

- Easy Apply opens a form with several steps in the same tab. Advance with Next or Review.
- Click Easy Apply once. The dialog shows a "Dismiss" button before its fields load: wait 2 seconds and read again. A second click on Easy Apply closes the dialog.
- LinkedIn fills contact fields from the candidate's profile (email, phone country code, phone). Keep those values.
- The resume step shows LinkedIn's saved resumes (often 25 or more, each with a download link), then the button "Upload resume". The page can have NO file input until you click that button (`file_upload` on the button fails with "Element is not a file input"). So:
  1. Click "Upload resume" by its ref, once (action_summary "Adds a file input for the resume"). No system dialog appears.
  2. Call read_page with `filter: "all"` and no ref_id. The new file input is at the END of the page, outside the dialog, as `button [ref_N] type="file"` with no name. If the page is cut, it is in the list of elements below the cut. The interactive view never shows it.
  3. `file_upload` with that ref and the PDF from the brief. The new file becomes the selected resume.
- Work experience and education can come pre-filled from the LinkedIn profile. Keep them.
- A step can hold a privacy-notice declaration checkbox. Tick it and list it in `declarations`.
- A step can hold voluntary diversity questions. Use "Prefer not to say" when it is offered.
- The last step shows the final submit button. Stop there with status "parked".
- The guard hook can refuse a click on Easy Apply, Next or Review with the text that the click can send the application. That refusal is final, and the same click is refused again in the next run. Stop at once with status "parked", report the step in `where` and the open dialog in `open`, and do not try the click again or another route to it.
- A job whose only control is "Apply on company website" links through a safety redirect. Navigate your tab to the href once. If the tab shows a browser error page, reload at most twice, then stop with `LOAD_FAIL`.
