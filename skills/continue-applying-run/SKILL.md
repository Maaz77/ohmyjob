---
name: continue-applying-run
description: Internal step of /ohmyjob:continue-applying that runs the application queue in continue mode in the application-master agent. Use only when the continue-applying skill says so.
user-invocable: false
context: fork
agent: ohmyjob:application-master
background: false
---

Run the application queue now in continue mode ("continue"): follow "Continue mode (/ohmyjob:continue-applying)" in your steps.
