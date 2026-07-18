# Plan

1. Add red integration coverage around `RegexTableLinker.link` with temporary C source.
2. Add a source-language helper that lexes real C string tokens, groups only trivia-separated adjacent tokens, concatenates their contents, and maps logical offsets back to raw offsets.
3. Run configured regexes over raw text plus logical C string views; deduplicate and use the entity offset for evidence.
4. Re-run the live-derived source verifier and Analyzer regression gates.
