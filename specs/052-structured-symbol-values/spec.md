# Structured symbol values for Rule/Example

## Goal

Give Rule/Example generation exact constant and declaration-initializer facts in
a structured form, so immutable values are reflected without treating mutable
variables' initializers as their runtime values.

## Contracts

- Referenced symbols are typed records, not preformatted text lines.
- Framework per-node and DBMS batch consumers serialize those records with one
  canonical JSON function; delimiter joins are forbidden.
- Constant values are marked `immutable_constant`; variable initializers are
  marked `declaration_initializer`.
- Every INIT_BY source is retained rather than last-source-wins.
- INIT_BY source symbols are also emitted as typed records, so a source name can
  be resolved to its exact declared value instead of becoming another vague label.
- Rule/Example prompts use exact immutable literals when they determine a
  condition, calculation, mapping, or result; a macro name alone is forbidden.
- Known literals such as `3`, `"PAID"`, `"SK-VAN"`, and exact return/status codes
  must not be generalized to "value", "status", or "failure result".
- Variable initializers may be used only when the body has not reassigned the
  variable before the relevant use. No deterministic global substitution is
  performed.
- Outputs remain the existing typed Pydantic Rule/Example schema.
- If an exact runtime value is required but cannot be established, the output
  creates a specific question instead of inventing a value or using vague prose.

## Failure scenarios

- An expression-valued constant is not falsely evaluated if the parser did not
  provide a resolved literal.
- A mutable variable's declaration initializer is not presented as its current
  value after assignments.
- Missing values remain explicit empty fields and are not guessed.
- A variable name is not emitted as though it were a function's actual return value.

## Observed root causes (2026-07-14 latest prompt record)

- Structured records were introduced, but framework and DBMS consumers still
  called string `join`, leaving an incomplete migration and a runtime TypeError.
- The previous prompt saw exact constants such as `FREE_SHIP_AMOUNT=50000`,
  `BASE_SHIP_FEE=3000`, and `ST_ORDER_SHIP="40"`, yet some Example results still
  used vague phrases such as "실패를 반환한다" instead of the known return code.
- The Rule/Example prompt had no explicit contract distinguishing immutable
  literals, declaration initializers, mutable values, and multiple INIT_BY sources.
