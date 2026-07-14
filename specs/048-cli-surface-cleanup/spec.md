# CLI surface cleanup

## Goal

Keep `cli_scripts/` limited to supported Analyzer operator and maintainer entry
points, without presenting spec-local verification artifacts as general CLI.

## Requirements

- Preserve every tracked CLI with a current runtime, maintenance, CI, recovery,
  or documented regression consumer.
- Remove package markers because application code does not import
  `cli_scripts`; these files are direct entry points.
- Do not delete or relocate untracked user WIP.
- Document the owner, purpose, and destructive risk of each public CLI.

