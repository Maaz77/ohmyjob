# SmartRecruiters (jobs.smartrecruiters.com Easy Apply)
hosts: jobs.smartrecruiters.com, *.smartrecruiters.com

- The `oneclick-ui` form is built from `spl-*` web components inside open shadow roots. `read_page` cannot see into them; the helper extension puts a light-DOM proxy over each one, so the fields DO appear in the interactive view. If the page shows only `"Apply With Indeed"` and nothing else after two reads, the helper is not running: stop with status "blocked", blocker `NOT_ACCESSIBLE: SmartRecruiters shadow DOM`.
- Never click `"Apply With Indeed"`: it leaves the form for another site.
- Only the controls in the viewport are listed. The form is long (personal information, Experience, Education, Your Profiles, a cover-letter box, then `"Next"`), so scroll and call `read_page` again rather than reporting a missing field.
- Phone number: a button named `"Country code"` opens a searchable list. Fill the textbox `"Search by country/region or code"`, then click the option. The dialling code comes from the `Phone` line of Profile.md.
- The resume file input is named `"Choose a file or drop it here"`. The page has two of them; the second is named `"Choose a file or drop it here (2)"`. Use `file_upload` on the first.
- `"Add"` buttons under Experience and Education open extra fields.
- The form has several steps; the step button is named `"Next"`.
