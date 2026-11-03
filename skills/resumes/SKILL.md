---
name: resumes
description: Write one role resume (yaml) per target-role family from Profile.md, build each into a PDF, fix overflow, and show every PDF for approval. The PDFs are what /ohmyjob:prep-jobs screens against. Use when /ohmyjob:setup says so or the user types /ohmyjob:resumes.
allowed-tools: Read, Write, Edit, AskUserQuestion, Bash(ohmyjob *)
---

# Build the role resumes

Prep-jobs picks, for each job, the pre-made resume that fits it best, then tailors a copy. It only knows the roles that have both `Default_Resume_Files/<Role>.yaml` and `<Role>.pdf`. This skill creates those pairs.

A line of command output that starts with `ohmyjob: ` or `FATAL` is an error: show it and stop (a `FATAL` naming `ohmyjob.json` means a setting is invalid: fix that key with the user).

## 0. Before you start
- Read `Profile.md` and `Default_Resume_Files/base.yaml`. If either is missing, tell the user to run `/ohmyjob:profile` first and stop.
- Read the plugin's `templates/role.yaml` for the shape of a role file, and `Default_Resume_Files/` for existing role files. Never overwrite an existing `<Role>.yaml` or `.pdf`: ask first, or pick another name.

## 1. Truth rules
The truth rules for a resume are in the plugin's `agents/resume-worker.md` (sections "Source pool and truth boundary", "Never" and "Always"). Read that file now and follow those rules for every sentence you write here: nothing outside `Profile.md` may appear, no fact is invented, upgraded or borrowed, numbers are copied exactly, no LaTeX in the yaml. They are not repeated here.

## 2. Choose the role families
From `# Fit Preferences` list the target-role families, one resume each (group titles a hiring manager would treat as the same job). Show the list; the user confirms, merges or adds. File names are `Role-Name.yaml` (letters, digits and hyphens).

## 3. Write each role yaml
For each family write `Default_Resume_Files/<Role-Name>.yaml` in the shape of `templates/role.yaml`:
- `title`: a common, natural name for the role, no seniority word the candidate's record does not carry.
- `introduction`: two or three sentences that open with exactly that title, in the first person of the candidate's voice, using the strongest evidence for this role first.
- `experience`: every role of every employer from `Profile.md`, in its original order, with 2 to 4 bullets each (more only where the source has more), reordered so the evidence for this role family leads. Same employer, several roles: use the grouped `employer:` / `roles:` shape. Never change an employer, title, place or date.
- `projects`: the most relevant ones, or `[]`.
- `skills`: flat rows `label` + `items`, relevant ones first; at most 106 characters of items per row. A bullet is at most 248 characters.
- `interests: no`, `education_details: no` unless the user wants them printed.
Show each yaml to the user before writing it, and write it after their yes.

## 4. Build, fix, show
For each yaml run `ohmyjob resume build Default_Resume_Files/<Role-Name>.yaml`. It prints one line:
- `OK <pdf>`: show the user the PDF (read the PDF with the Read tool, or give its path) and ask for approval with AskUserQuestion: approve, or what to change.
- `OVERFLOW <N> pages`: the resume is longer than `resume_max_pages`. Take the smallest step and build again after each: shorten the longest bullets and the introduction without losing a fact; then drop the single least relevant bullet (never below 2 per role); then the least relevant skills items; then the least relevant project. Never change font, margins or spacing. Tell the user what you dropped.
- `FAIL <reason>` or a `CHECK-FAIL` list (a line over the character budget, a missing key): fix the yaml as the message says and build again. Stop after four tries for one yaml and show the user the message.
Apply the user's change requests to the yaml and rebuild. A resume counts as done only after the user approves its PDF.

## 5. Finish
List the approved pairs. Tell the user the next steps: `/ohmyjob:find-jobs` to fetch jobs and `/ohmyjob:prep-jobs` to screen them and tailor a resume per job. If a resume is not approved, say which one is still open.
