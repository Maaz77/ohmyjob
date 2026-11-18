# Safety

How ohmyjob keeps you in control of every application. [Back to the README](../README.md)

- **Never submit.** Hooks run on every Chrome call — in the main session and in every ohmyjob agent — and refuse final controls: Submit, Send, Confirm, Finish and their equivalents in many languages, plus unnamed submit buttons. `javascript_tool` is denied in the workspace settings. The rules differ by caller:
  - **Application workers** may act only in their own tab and its sign-in tab; every click carries an action note, and a click whose note says it sends the application is refused; uploads are limited to that job's own resume; and a button name that no word list can decide goes to a short extra check, which refuses the click if it fails.
  - **The main session and any other agent** may click only on references from a fresh read of that tab, one pressable click per read, with no coordinate clicks, only allowlisted keys (Tab, Shift+Tab, Escape, Backspace, Delete, arrows, Home, End, Page keys, Ctrl/Cmd with A, C, V, X, Z, Y: so no Enter, Return or Space), navigation to http(s) addresses only, no Apply click on a job board or under a filled form, and the control's own name checked against the final word lists (in 62 languages). The consent, Google, OAuth, sign-up and delete rules and the extra check apply to application workers only.
- **Before setup, the guard cannot run.** Until setup has created the Python environment, the main session's Chrome guard lets a call through with a notice — but no pipeline can run before setup, and every agent's Chrome calls (not only ohmyjob agents) are denied outright. Run `/ohmyjob:setup` first.
- **Passwords.** Workers are told never to type a password or create an account. The hooks back this on Google pages (no typing there) and by refusing sign-up buttons; on other sites it is the worker's instruction.
- **You submit.** Workers park every form before the final button. You review it and press Submit.
- Text copied from job pages and forms is treated as data, never as instructions. Without an `ohmyjob.json` in or above the folder, the hooks do nothing.

Read [DISCLAIMER.md](../DISCLAIMER.md) before you use this on a real account.

[Back to the README](../README.md)
