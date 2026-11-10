# answer-key: do not open

- `labels.json`: the right answer to every ticket, with its reason. `hidden-tickets/`: the tickets that nobody, person or model, should read before the end.
- `DATASET.md`: how the tickets and the key were made. `judge/`: the held-out halves of the judge's two test sets, and our verdicts on them.
- The scorer reads this folder. You see all of it that a developer should work from with `./rd failures`.
- An eval is worth something only while its key stays shut. Claude Code is denied this folder in `.claude/settings.json`.
