# Plan

1. Add red load-AST tests that request stop or pause after the first file.
2. Make the shared pause gate an actual asyncio scheduling checkpoint.
3. Add cooperative gate calls at bounded AST file/link/persist/stream boundaries.
4. Run focused and authoritative regression gates.
5. Restart the current Analyzer service and capture a live stop-to-stream-end
   measurement during AST loading.
