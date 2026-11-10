# Opus & Sonnet refund desk

A hands-on workshop in **eval-driven agent building** on **Claude Managed Agents**.

"Opus & Sonnet" is a made-up online store for Claude-themed goods. It has a queue of refund tickets: a customer's message, a photo, and records
in the store's systems. A claims agent handles each one: it **decides** (refund, replace, deny or escalate), **cites the policy**, and **writes the reply**. It looks up what it needs with tools, and every ticket
has a checked right answer, so every version of the agent gets a score. You make the score go up.

## Start

```bash
./start.sh
```

That opens the dashboard (http://127.0.0.1:8321, on this laptop only). **Nothing is installed:** all it needs is python3, 3.9 or newer (on a Mac it
comes with Apple's command line tools; `./start.sh` says so if it is missing). The first page takes your API key and does the rest: it deploys your
agent, checks the deployment, and runs one live ticket to prove that it all works. Then follow the pages, top to bottom.

If macOS refuses `./start.sh`: `bash start.sh`. On Windows, use WSL.

With Claude Code: open it in this folder. `CLAUDE.md` tells it the loop and the rules.

## The loop

1. **Run** your agent: `./rd run`, or the Run button. Every ticket is one session on Managed Agents, many at once, scored as they land.
2. **Read the claims it got wrong.** Are the facts right and the rule wrong? Then the guide is the problem.
3. **Fix one failure mode**: edit `my-work/agent/handbook/SKILL.md`.
4. **Run again and compare**: `./rd run --note "what I changed"`, then `./rd diff`: what the change fixed, what it broke, and whether that is more than chance.
5. **Test the judge**, then make it better: `./rd grade --test`, at the end `./rd grade --test --unseen`.
6. **Test on claims nobody has read**: `./rd run --set hidden`.

You have one agent, `my-work/agent/`, and **its versions log themselves**: when a run finds the files changed, it keeps them as
`my-work/versions/<n>/` and writes number, time, note, files changed, the platform's agent version, score, cost and time into
`my-work/versions/LOG.md`. Nothing to copy, nothing to name. `./rd restore 2` goes back to version 2.

## The folders

```
my-work/        THE ONLY PLACE YOU AND CLAUDE CODE CHANGE: your agent; the harness logs its versions and runs here
shop/           the made-up store: tickets, what the tools serve, the handbook           read-only
answer-key/     the right answers, and the hidden tickets                                 do not open
harness/        the machinery: runner, Managed Agents backend, tools, scorer, dashboard   read-only
workshop/       the words of the dashboard's pages                                      read-only
```

Each folder has a short README. `./rd --help` lists the commands.

## Good to know

- **Your key** stays in the dashboard's memory: no file, no log. `./rd` in a terminal hands live commands to the running dashboard.
- **24 claims to work on, and 8 more that start hidden.** `./rd new-tests on` (or the button on page 4) turns them into new tests: run them with
  `./rd run --set new`, and switch the dashboard between them and the 24 at the top. A run takes all 24. On 24 claims a score is good to about ±18 points: `./rd diff` says whether a change is
  more than chance, and `--repeat 3` gives a mean.
- **The tools are deployed with the agent.** The look-ups are small scripts that go to the platform as skills, with the store's made-up records, and run
  in each session's own sandbox, which has no network. The decision comes out through one typed tool, `submit_decision`, and code checks it again here.
  (With real customers' data, follow the public guide on secure deployment: look-ups as custom tools that run in your own code.)
- **At the end**, `./rd cleanup` removes what your key created on the platform.
- The store, its customers and its carriers are made up. The photos are 3D renders. Some tickets carry hidden instructions meant to mislead AI agents.

How it works inside: `harness/README.md`.
