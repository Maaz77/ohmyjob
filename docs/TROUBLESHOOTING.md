# Troubleshooting

What the errors mean, and what a run costs. [Back to the README](../README.md)

## What it costs

Each run ends with a usage table: agents, API calls, and input, cache and output tokens per agent type and model. The defaults are deliberately modest so that a first round fits inside a Pro plan — screening uses a small model, `results_per_term` is 30, and the number of workers running at once is Claude Code's subagent cap (20) minus one. Raise them once you know what a round costs you.

## When something goes wrong

A line of output starting with `ohmyjob: ` or `FATAL` is an error. Read it, fix what it names, run the command again.

- **`ohmyjob: environment missing or stale, run /ohmyjob:setup`** — the Python environment is missing or does not match this copy of the plugin. Run `/ohmyjob:setup`.
- **`FATAL` naming `ohmyjob.json`** — a setting is invalid; the message names the key.
- **A line saying a file "still has the template marker"** — replace the mock data in that file with your own and delete its marker line.
- **The tracker is open in Excel or LibreOffice** — saving is refused with a clear message and nothing is lost. Close the file and run the command again. The same happens if the tracker changed on disk during a long run.
- **The hooks seem inactive** — Claude was probably started outside the project folder, or the plugin is installed only at user scope. Check with `claude plugin list`.
- **You switched between `--plugin-dir` and an installed copy** — that changes the plugin's data folder. Run `/ohmyjob:setup` again after each switch.

[Back to the README](../README.md)
