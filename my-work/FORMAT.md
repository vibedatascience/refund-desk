# How everything is logged

Everything that is tried here lands under `my-work/` as plain files, in one format. The dashboard draws whatever lies there, within a second,
whoever wrote it. Nothing is kept anywhere else. **Scores are never stored:** the harness grades the decisions against the answer key each time it reads them.

```
my-work/agent/                 the agent as it is now: prompt.md, handbook/SKILL.md, agent.json        you edit
my-work/versions/LOG.md        the log, for people; log.json, the same for programs                    the harness writes
my-work/versions/<n>/          the files of version n, as they ran                                     the harness writes
my-work/runs/<run>/            one folder per run                                                      the harness writes
my-work/experiments/<name>/    a run of your own making, same format as a run                          you write
```

## A version: one entry in `versions/log.json`, under `"versions"`

| field | |
|---|---|
| `version` | "1", "2", ...: given by the harness when a run or a deploy finds files that match no earlier version |
| `time`, `ts` | when it was logged: local time as text, and seconds since 1970 |
| `note` | one line: what changed and why (`./rd run --note "..."`) |
| `from`, `files` | the version it grew out of, and per changed file the lines `added` and `removed` |
| `print` | a hash of the files: same files, same version |
| `platform` | `agent_id` and `agent_version` on Managed Agents, once deployed |

`"runs"` in the same file holds one line per finished run (version, model, effort, right, n, cost_usd, seconds): a copy for reading; the run's folder is the source.

## A run: a folder with two files

**`run.json`**, the settings. Required: `agent` (the version, as text) and `model`. Optional: `note`, `effort`, `backend` (how it was made:
`managed-agents`, or a word of your own), `take` (1, 2, ...: repeats), `started` ("2026-11-10 15:40:00", local time; `started_ts`, the same in seconds since 1970), `concurrency`, `platform`.

**`events.jsonl`**, what happened: one JSON object per line, in order of time. Every line has `ts` (seconds since 1970) and `type`.
The line that counts is a ticket's decision. If a ticket has several, the last one holds.

```json
{"ts": 1794190699.8, "type": "case", "id": "RD-0008", "status": "done", "result": {"answer": {"decision": "deny", "amount": 0}}}
```

| in `result` | | |
|---|---|---|
| `answer.decision` | required | refund, replace, deny or escalate |
| `answer.amount` | required | dollars refunded; 0 unless the decision is refund |
| `answer.reason`, `.report`, `.team`, `.handbook_section`, `.photo_observation` | optional | text |
| `answer.injection_detected` | optional | true or false: the agent's flag for planted instructions |
| `seconds`, `cost_usd` | optional | wall time and list price of this ticket |
| `sessions` | optional | a list, one object per session: `session_id`, `model`, `started_ts`, `ended_ts`, `seconds`, `usage` (tokens), `cost_usd` |

Other lines, all optional: `{"type": "start"}` and `{"type": "end", "reason": "finished"}` (between them the dashboard shows the run as running);
`case` with `status` queued, running, retrying or failed (failed = no answer: counts as wrong); `notice` (`kind`, `text`); `report` (`markdown`, `cost_usd`);
`deployed` (`agent_id`, `agent_version`). The harness also keeps `sessions/<session id>.json` (each session's record as fetched from the platform) and
`agent-files.json` (the files that ran).

## An experiment of your own

Anything that decides tickets can be put beside the runs: another prompt style, a rule written in code, a baseline that always escalates.

```bash
mkdir -p my-work/experiments/always-escalate
echo '{"agent": "always-escalate", "model": "none", "backend": "a script", "note": "baseline: send everything to a person"}' > my-work/experiments/always-escalate/run.json
for t in shop/tickets/RD-*; do echo "{\"ts\": $(date +%s), \"type\": \"case\", \"id\": \"$(basename $t)\", \"status\": \"done\", \"result\": {\"answer\": {\"decision\": \"escalate\", \"amount\": 0}}}"; done > my-work/experiments/always-escalate/events.jsonl
./rd check always-escalate      # says in plain words what is wrong, if anything
./rd score always-escalate      # and it is on the dashboard, marked as made outside the harness
```

The folder's name is letters, digits, dots and hyphens. Hidden tickets are passed over in experiments: only the harness runs those.
