---
name: build-job-resumes
description: Build the tailored resume PDF of the named job folders and set each row to "Resume Built". Use after correcting a tailored yaml by hand, and only when the user types /ohmyjob:build-job-resumes <name, name, ...>.
disable-model-invocation: true
allowed-tools: Bash(ohmyjob *)
---

1. Read the text the user typed after `/ohmyjob:build-job-resumes` (`$ARGUMENTS`). It is a comma-separated list of
   job names. Refuse, with one line and no command, when it is empty, longer than 300 characters, or holds a
   character outside `A-Z a-z 0-9 space , . _ & + ( ) -` (a quote, `$`, backtick, backslash, `!`, `;` and the like).
   Say which character and stop.
2. Run `ohmyjob resume build-jobs "<the text, as typed>"`.
3. Report the `OK`, `FAIL`, `WARNING` and `FATAL` lines as printed. A `FAIL ... no folder matches` or
   `FAIL ... ambiguous` means the user must name the job more exactly: do not guess a folder, and do not run the
   command again with another name.

Never edit a yaml to fix a failed build: show the reason. `/ohmyjob:build-resumes` builds the default resumes of
`Default_Resume_Files/` and is a different command.
