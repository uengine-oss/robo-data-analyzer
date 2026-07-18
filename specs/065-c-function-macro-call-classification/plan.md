# Plan

1. Reproduce the false external edge with a temporary C source fixture.
2. Inventory function-like definitions from the existing run-scoped source
   cache and classify those call sites before stdlib/external fallback.
3. Preserve object-like and undeclared-call behavior with opposite cases.
4. Run focused, full, and live relation verification.
