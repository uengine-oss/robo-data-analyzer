# Data Model: Refactor Evidence

## Responsibility Map

- **Fields**: responsibility name, target directory, allowed dependencies, public entry points, owned configuration.
- **Invariant**: one active home per responsibility; dependencies point inward toward lower-level owners.

## Move Record

- **Fields**: old path, new path, reason, static consumers, dynamic/external consumers, migration slice, local gate.
- **State**: `planned → moved → references-updated → locally-verified → globally-verified`.
- **Invariant**: an old path cannot reach `globally-verified` while any active reference remains.

## File Audit Record

- **Fields**: baseline path, final path, responsibility, producers/consumers, findings by principle category, verdict,
  change reference, verification evidence.
- **Verdict**: exactly one of `fixed`, `deleted`, `no-violation`.
- **Invariant**: every baseline and newly created file has one final record; `pending` count is zero at completion.

## Regression Evidence

- **Fields**: gate, command/scenario, expected result, actual result, timestamp, artifact or log reference.
- **State**: `not-run → passed | failed | blocked`.
- **Invariant**: failed or not-run required gates prevent completion.
