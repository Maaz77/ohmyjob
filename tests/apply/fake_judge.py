"""A fake Haiku check for the tests (set AA_JUDGE_CMD to run it instead of claude).

It reads the prompt on stdin and prints a result in the form of `claude -p --output-format json`.
Every call is written as one line to the file in AA_FAKE_JUDGE (when set), so a test can count calls.
"""
from __future__ import annotations

import json
import os
import re
import sys

BLOCK_NAMES = {"trasmetti la domanda", "invia candidatura", "envoyer", "absenden", "ok", "continue with google"}
FAIL_NAMES = {"fallisci"}

def reply(obj: dict) -> None:
    usage = {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 5}
    print(json.dumps({"type": "result", "is_error": False, "result": json.dumps(obj), "usage": usage}))


def field(prompt: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}: (.*)$", prompt, re.M)
    if not match:
        return ""
    raw = match.group(1).strip()
    try:
        return str(json.loads(raw))
    except json.JSONDecodeError:
        return raw


def main() -> None:
    prompt = sys.stdin.read()
    log = os.environ.get("AA_FAKE_JUDGE")
    if "Decide about ONE click" in prompt:
        name = field(prompt, "Control name")
        if log:
            with open(log, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"kind": "click", "name": name}) + "\n")
        if name.lower() in FAIL_NAMES:
            print("not json at all")
            sys.exit(1)
        if name.lower() in BLOCK_NAMES:
            reply({"verdict": "block", "reason": "fake: final", "context_dependent": False})
        else:
            reply({"verdict": "allow", "reason": "fake: navigation", "context_dependent": name.lower() == "procedi"})
        return
    print("not a click prompt")
    sys.exit(1)


if __name__ == "__main__":
    main()
