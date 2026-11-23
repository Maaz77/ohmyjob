# ADR 0001: Mid-step controls that carry a final word

- **Status:** accepted
- **Date:** 2026-11-23
- **Issue:** [#22](https://github.com/Maaz77/ohmyjob/issues/22)

## Context

A **final control** is matched on its name alone (`FINAL` in `scripts/lexicon.py`). Two places read
that list and neither knows what step of a form it is looking at:

- the **guard** refuses the click and orders a park (`check_click_name` in `scripts/hooks.py`);
- the **finish gate** sees the same control still on screen and grades the park PASS
  (`check_result` in `scripts/aa_lib.py`), which sends the job to **Pending Review**.

So a date picker's "Done", a multi-select's "Confirm", a screening step's "Complete" stops the
worker at step 2 of 4 and then certifies the stop as the end of the application. One run produced
14 such **false parks**. The second half is the real damage: a half-filled application in Pending
Review looks finished, and the candidate is the one who presses Submit on it.

`never-submit` is not in question. The problem is that a name is a weak signal and it was the only
signal.

## Decision

### 1. The two ends fail closed in opposite directions

When a check cannot tell a mid-step control from the real submit:

- the **guard denies**. An unsure click is never allowed.
- the **finish gate grades BLOCKED**. The job goes to Needs Attention.

The costs are not symmetric. A wrong gate costs the candidate a glance at a job that needed none. A
wrong guard sends an application. Every later change to either end holds this asymmetry, and the
temptation to make the gate lenient "to reduce noise" is the thing this clause exists to refuse.

### 2. A widget role excuses the word; the judge still decides

`widget_ancestor` (`scripts/aa_lib.py`) returns the nearest enclosing `listbox`, `menu`, `menubar`,
`tree`, `treegrid`, `grid` or `combobox`. A final-word control inside one of those skips the hard
deny and falls through to the existing Haiku **judge**, carrying the widget's name as context. The
judge fails closed (`judge_failed` raises), so nothing is allowed on its name alone.

Rejected alternatives:

- **Allow the word outright** (add "Done" to `SAFE`): a bare name allow, and a form whose submit is
  named "Done" or "Complete" would be clickable.
- **"Another final control exists elsewhere, so this one is not it"**: Easy Apply renders only the
  current step, so no outer Submit exists at step 2 of 4 — it fails on exactly the reported case.
- **"A step control is visible nearby, so this is not the last step"**: unsafe in the guard, because
  any stray "Next" would excuse a real Submit. Also unsafe in the gate: `SAFE` is not a step list,
  it holds `edit`, `close`, `save`, `yes`, and LinkedIn's final review step shows Edit buttons
  beside the real Submit, so this rule would fail the parks that should pass. A narrow step list is
  the kind of hand-kept multilingual vocabulary this change is trying not to add.

### 3. `dialog` is not a widget role

A real date picker often renders as `role="dialog"`, so leaving `dialog` out is a known gap. It is
deliberate: the submit of an Easy Apply dialog, and of every modal application form, sits inside a
`dialog`, so including it would excuse the real thing. No fixture in `tests/apply/samples/` contains
a `grid`, `gridcell`, `menu` or calendar line, so this repo has no attested picker markup to widen
the set against. Guessing at markup is how `never-submit` breaks.

A dialog-shaped picker therefore still gets refused by the guard. Decision 4 stops that refusal from
being recorded as a finished application, which is the half that matters.

### 4. `final_button` is a claim, not evidence

The gate used to grade PASS when the worker named a `final_button` even though no final control was
in the last snapshot. A worker's report is not a verdict. PASS now requires a final control present
in the snapshot itself, outside any widget. The worker contract already requires the last browser
call to be a `read_page` on the page where it stopped, so a genuine last step shows the control.

### 5. The main session's check stays strict

`foreign_click_problem` (`scripts/hooks.py`) is a third reader of `FINAL`, covering the main session
and any non-ohmyjob agent. It gets no widget path. It has no judge to defer to, so a carve-out there
would be the bare name allow that decision 2 rejects — and the human can click the picker themselves.
The asymmetry between the two guard paths is intentional, not an oversight.

## Consequences

- A worker can finish a form whose picker closes with "Done": the job reaches the real last step.
- A park on a dialog-shaped picker now grades BLOCKED instead of PASS. Fewer false Pending Reviews,
  more Needs Attention rows. That trade is decision 1, on purpose.
- Any job that used to pass on a `final_button` claim alone now needs the control in the snapshot.
- Reversibility: two-way for the gate (one condition), two-way for the guard. Nothing in here
  loosens `never-submit`, so a revert costs grading accuracy, never a sent application.
- Issue [#23](https://github.com/Maaz77/ohmyjob/issues/23) covers the same shape one rung up — an
  apply word inside a picker ("Apply filters", "Apply" in a date picker). It is untouched here and
  can call `widget_ancestor` when it is worked.
