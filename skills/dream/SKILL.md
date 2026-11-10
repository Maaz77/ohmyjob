---
name: dream
description: Turn the traces of the last application runs into ATS notes for this workspace. One Opus master groups the new traces by ATS key and starts one worker per key that writes the note. Use only when the user types /ohmyjob:dream.
disable-model-invocation: true
context: fork
agent: ohmyjob:dream-master
background: false
---

Run the dream pass now. At the end, show the master's summary and these three lines:
- The notes of this workspace are in `ohmyjob/ats/`; a note there replaces the plugin's note of the same name.
- The report of this pass is `Dream_Report.md` in the workspace.
- Undo the whole pass (the notes, the report and the watermark): `ohmyjob dream reject`.
