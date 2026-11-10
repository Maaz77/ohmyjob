# Phenom careers sites ("Apply Now" with an `/apply` or `/hvhapply` href)

- The "Apply Now" button has an `href`. Do not click it. Read the button with `read_page` and its `ref_id` (the short view ends in `?…`), then navigate YOUR tab to the full href.
- An `/hvhapply?...` address can be a redirect: the page looks like the job page for a few seconds, then it goes to `/apply?...&step=1&stepname=personalInformation`. Wait up to 10 seconds, then call `read_page`. Never navigate or click again before that: it restarts the redirect.
- Step 1 has "Upload from PC" and a file input. Upload the resume there first (`file_upload` on the file input ref).
- Some sites show an alert "File name is too long" for a long PDF name and then hang the page ("Page still loading"). That is an `UPLOAD_HANG` blocker (see "Resume upload" in your rules).
