"""Daily productivity & AI-leverage report.

Answers, per operator and team, the five questions Ali asked:
  1. Usage      — how much are people using the new system?
  2. Throughput — how many tasks are they completing?
  3. AI leverage— ratio of AI usage to total usage (two views).
  4. Speed      — does the process speed work up (vs a pre-launch baseline)?
  5. Effective  — are they genuinely MORE productive, or just faster?
                  (the productivity-paradox guard.)

Layers (CLAUDE.md):
  aggregate.py — pure KPI math + verdict; no I/O, fully unit-tested.
  baseline.py  — pre-launch reference (median cycle + weekly throughput).
  render.py    — email-safe HTML.
  runner.py    — load -> aggregate -> render -> deliver (delivery gated OFF
                 by default), mirrors execution/products/pilot/dash_runner.py.

See directives/productivity-report.md for the KPI catalog + verdict rubric.
"""
