# Plan

1. Search code, workflows, README, and specs for every tracked CLI filename.
2. Retain referenced operational tools and remove only proven dead packaging.
3. Remove spec-local, untracked commands from the public CLI guide without
   changing the user's local WIP file.
4. Compile every retained CLI and verify no `cli_scripts` package imports exist.

