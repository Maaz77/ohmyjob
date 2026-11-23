# Glossary

The words this repo uses for the apply stage, and the one file that owns each.

This file defines terms; it does not explain mechanisms. Each entry points at the file that
holds the real rules, so a term has one home. Prefer these words in issues, test names, commit
messages and ADRs over synonyms.

For the job-discovery stage, read `.claude/jobfind-scrape.md` first.

## The run

| Term | Meaning | Owner |
|---|---|---|
| **application worker** | The subagent that works one job in one Chrome tab and reports one JSON object. | `agents/application-worker.md` |
| **guard** | The `PreToolUse` hook on every Chrome call. It refuses an unsafe call before the browser sees it. Rules differ by caller: a worker, the main session, any other agent. | `scripts/hooks.py`, `docs/SAFETY.md` |
| **judge** (Haiku check) | The short extra check for a control name that no word list can decide. It fails closed: an unanswered check denies the click. | `scripts/judge.py`, `Ctx.judged_click` |
| **snapshot** | The saved text of the last `read_page` on a tab. An indented tree of `role "name" [ref_N]` lines. Every later check reads it, never the live page. | `scripts/aa_lib.py` (`snapshot_path`, `parse_refs`) |
| **refs** | The flat map of `ref_N` to that element's role, name and attributes, parsed from a snapshot. It keeps no parent or depth. | `scripts/aa_lib.py` (`parse_refs`) |
| **marker** | A line the helper extension injects (`[AA alert]`, `[AA frame]`). Never a real control; every click on one is refused. | `scripts/hooks.py` (`marker_problem`) |
| **sticky tab** | A tab of `/ohmyjob:continue-applying`, where a form may already stand part-filled from an earlier run. | `scripts/hooks.py` (`sticky_tab`) |
| **sign-in lease** | The one-Google-sign-in-at-a-time claim per host. | `scripts/hooks.py` (`check_signin_lease`) |

## Controls on a page

| Term | Meaning | Owner |
|---|---|---|
| **never-submit** | The invariant: no agent ever clicks the control that sends an application. The candidate submits personally. Everything below exists to serve it. | `docs/SAFETY.md` |
| **final control** | A control whose name holds a submit word in any listed language — Submit, Send, Confirm, Finish, Done, Complete — plus a one-click Apply variant. | `scripts/lexicon.py` (`FINAL`) |
| **apply word** | A word that *starts* an application rather than sending it. Final only on a tab that already showed a form. | `scripts/lexicon.py` (`APPLY`) |
| **step control** | A control that moves within a flow and sends nothing: Next, Continue, Review. Matched on the whole name. | `scripts/lexicon.py` (`SAFE`) |
| **widget control** | A control inside a picker, listbox, menu or tree — a date picker's "Done", a multi-select's "Apply". It carries a final word but closes a widget, so it is not the application's submit. | `scripts/aa_lib.py`, ADR 0001 |
| **consent carve-out** | The rule that excuses a cookie or consent dialog's own save button, judged by the dialog around it rather than the button's name. The precedent **widget control** follows. | `scripts/aa_lib.py` (`consent_control`), `scripts/hooks.py` (`consent_banner`) |
| **note** | The worker's own English sentence for a click (`action_summary`). A note that says the click sends the application is refused on its own, whatever the button is called. | `scripts/aa_lib.py` (`note_problem`) |

## Tab flags

| Term | Meaning | Owner |
|---|---|---|
| **filled** | This tab showed something shaped like a form — three or more non-search fields. Set by the shape of a page, so a job page with search boxes can set it. Not proof that anyone answered anything. | `scripts/aa_lib.py` (`looks_like_form`) |
| **acted** | The worker itself filled, uploaded, ticked or chose something on this tab. The honest flag; prefer it over **filled** when asking "did work happen here". | `scripts/hooks.py` (`park_order`), tab state |

## Finishing a job

| Term | Meaning | Owner |
|---|---|---|
| **parked** | The worker reached the last step and stopped before the final control. The good outcome. | `agents/application-worker.md` |
| **blocked** | The worker could not go on: a question for the candidate, a closed job, an account wall, a refused step. Needs a human or another run. | `agents/application-worker.md` |
| **finish gate** | The check that grades a worker's report against the last snapshot. Returns PASS, BLOCKED, FAIL, ALARM or CLOSED. A worker's claim is not a verdict. | `scripts/aa_lib.py` (`check_result`) |
| **false park** | A worker that stopped mid-flow but reported **parked**, usually because the guard refused a mid-step control. | ADR 0001 |
| **Pending Review** | The Chrome tab group for a PASS job: filled, parked, waiting for the candidate to press Submit. | `scripts/aa.py` |
| **Needs Attention** | The tab group for everything else. A job here costs a glance; a wrongly-passed job in Pending Review costs an application. | `scripts/aa.py` |
