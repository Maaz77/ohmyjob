"""A fake Haiku check for the tests (set AA_JUDGE_CMD to run it instead of claude).

It reads the prompt on stdin and prints a result in the form of `claude -p --output-format json`.
Every call is written as one line to the file in AA_FAKE_JUDGE (when set), so a test can count calls.
"""
from __future__ import annotations

import json
import os
import re
import sys

# The last entries mirror the real prompt's account-creation rule, so a sign-up refusal stays pinned by a test now
# that the word rule defers to the judge.
BLOCK_NAMES = {"trasmetti la domanda", "invia candidatura", "envoyer", "absenden", "ok", "continue with google",
               "create an account", "crea un account", "créer un compte", "konto erstellen", "registrarse",
               "criar conta", "account aanmaken", "zarejestruj się", "регистрация", "注册", "新規登録", "회원가입",
               "sign up", "register", "sign up with linkedin", "create account to apply"}
FAIL_NAMES = {"fallisci"}

# A delete control is judged from the container it sits in ("Inside"), never from the lines beside it: the same
# "Remove" sits inside an upload area (allow), inside a filled education row (block) and on a flat page with no
# container at all (block), with the same "Resume.pdf" next to it in the first and the last. One clause per name,
# mirroring the real prompt: the allow and the form-entry block read the name after the last "|", the container that
# directly encloses the control, while the account block reads every name. An upload area nested in a form named
# "Application form" must therefore pass, as it does on main.
# The name only decides that this rule applies, and it carries the prompt's capability framing: a delete-ish name that
# no word list knows ("Clear", "Reset") is judged the same way, because the real rule goes by what the click does.
DELETE_NAME_RE = re.compile(r"\b(delete|remove|clear|discard|reset|erase|trash|withdraw|rimuovi|quitar|remover"
                            r"|entfernen|löschen|elimina)\b", re.I)
ENTRY = ("education", "employment", "work experience", "reference", "address", "qualification")
ACCOUNT = ("account", "profile", "saved data", "my data", "sent application", "submitted")
RE_ADDABLE = ("resume", "cv", "upload", "attachment", "filter", "tag", "chip")

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
                # the container and the picker line as the prompt shows them, so a test can pin the evidence the real
                # model would read and not only the verdict that follows from it
                handle.write(json.dumps({"kind": "click", "name": name, "inside": field(prompt, "Inside"),
                                         "widget": field(prompt, "In a picker or list")}) + "\n")
        if name.lower() in FAIL_NAMES:
            print("not json at all")
            sys.exit(1)
        # the hook's own flag first, for a name the word lists caught but the prompt's name field cannot show (a
        # "Options" button over a child "Remove education", a name cut at 300 characters); the regex second, for the
        # capability cases no word list knows
        if field(prompt, "A delete word in the name or in the text inside the control") == "yes" \
                or DELETE_NAME_RE.search(name):
            inside = field(prompt, "Inside").lower()
            nearest = inside.rsplit("|", 1)[-1]
            ok = (any(w in nearest for w in RE_ADDABLE) and not any(w in nearest for w in ENTRY)
                  and not any(w in inside for w in ACCOUNT))
            # context_dependent is False on purpose: the real model may ignore the prompt line that asks for true, so
            # the guard must never cache a delete word's verdict by name, whatever the model says.
            reply({"verdict": "allow" if ok else "block", "reason": "fake: the container", "context_dependent": False})
        elif name.lower() in BLOCK_NAMES:
            reply({"verdict": "block", "reason": "fake: final", "context_dependent": False})
        else:
            reply({"verdict": "allow", "reason": "fake: navigation", "context_dependent": name.lower() == "procedi"})
        return
    if "filled the fields of ONE job application" in prompt:
        if "BOOM" in prompt:
            print("not json at all")
            sys.exit(1)
        if "BADFILL" in prompt:
            reply({"verdict": "suspect", "unsupported": [{"label": "Salary", "why": "not in profile"}]})
        else:
            reply({"verdict": "ok", "unsupported": []})
        return
    print("not a click prompt")
    sys.exit(1)


if __name__ == "__main__":
    main()
