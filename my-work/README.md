# my-work: the only place you and Claude Code change

- `agent/`: **your agent**, the one working copy: `prompt.md`, `handbook/SKILL.md` (the guide), `agent.json` (model, tools). Edit here.
- `versions/`: the harness's log of it. `LOG.md` says what ran, when, what had changed, and how it scored; `<n>/` holds the files of version n. Read, do not edit.
- `runs/`: your runs. The harness writes them; read them with `./rd score`, `failures`, `diff`, or on the dashboard.
- `experiments/`: runs of your own making, in the same format. `FORMAT.md` is one page on that format; `./rd check` tests a folder against it.
- `judge/`: your reply writers and judges, and their results.
