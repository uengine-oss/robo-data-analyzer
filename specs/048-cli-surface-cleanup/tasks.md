# Tasks

- [x] T001 Inventory every tracked and untracked CLI file.
- [x] T002 Search runtime, workflow, documentation, and spec consumers.
- [x] T003 Preserve eight referenced public tools and their hash ledger.
- [x] T004 Remove the unused empty package marker.
- [x] T005 Remove the untracked spec verifier from the public CLI table while
  preserving the user's local WIP file.
- [x] T006 Compile retained CLIs and verify the final Git diff.

## Evidence (2026-07-14)

- Seven retained Python entry points passed `py_compile`.
- Runtime imports of the `cli_scripts` package: 0.
- All tracked entry points have current README, source, maintenance, CI, or spec
  consumers. The untracked `verify_045_046.py` WIP was not deleted or moved.
