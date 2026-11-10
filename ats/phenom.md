# Phenom careers sites ("Apply Now" with an `/apply` or `/hvhapply` href)
hosts:

- The "Apply Now" button has an `href`. Do not click it. Read the button with `read_page` and its `ref_id` (the short view ends in `?…`), then navigate YOUR tab to the full href.
- An `/hvhapply?...` address can be a redirect: the page looks like the job page for a few seconds, then it goes to `/apply?...&step=1&stepname=personalInformation`. Wait up to 10 seconds, then call `read_page`. Never navigate or click again before that: it restarts the redirect.
- Step 1 (My information) has "Upload from PC" and a file input. Upload the resume there first (`file_upload` on the file input ref).
- The step 1 page can hang ("Page still loading", a 45 s executeScript timeout on read_page, form_input and file_upload). It hangs right after `file_upload` reports success, and it can also hang before any upload.
- Fail fast on that hang: right after a `file_upload`, report `UPLOAD_HANG` at once. With no upload yet, reload at most twice, then report `LOAD_FAIL`. Do not wait or poll.
- Keep the PDF name short: a long name can raise an alert "File name is too long" before the hang. A short name alone is not a proven fix.
- After a hung upload, step 1 stays empty (name, address, email, phone). A resumed run starts again at step 1.
