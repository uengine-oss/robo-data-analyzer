# External Agent and Skill Contract

## Physical isolation

```text
agents/skills/templates/
agents/skills/framework/<framework>/SKILL.md
agents/skills/external/<format>/SKILL.md
```

Framework Scan Agent receives only `FRAMEWORK_SKILLS_DIR`. External Agent receives only
`EXTERNAL_SKILLS_DIR`. Tests assert cross-catalog results are empty.

## Shared infrastructure

Allowed shared owners:

- structured submit-tool Agent runner
- LLM client and usage/cost middleware
- generic skill catalog/path boundary
- `gather_with_warmup`
- run-level `llm_semaphore`
- runtime logging and atomic file helper

Forbidden coupling:

- External Agent importing ScanResult or scan prompt.
- Scan Agent importing ExternalSkill or external prompt.
- one agent calling the other.
- external rules written into framework SKILL files.

## ExternalSkill schema

The submitted result contains only:

- skill name and normalized extensions/content signature
- one or more extraction rules
- key capture group and optional value capture group
- bounded explanation/provenance

Unknown fields are rejected. Expected customer keys, project names and literal allowlists are forbidden.

## Proposal/repair/consensus

- proposal plus at most two repairs (`R<=2`).
- repeated identical failure terminates early.
- consensus uses n=7, agree>=4 on extracted result sets, not regex text equality.
- every sample runs under shared semaphore and usage accounting.
- cost/pause/cancel aborts the whole agent operation and is awaited.

## Deterministic gate

A skill is accepted only if all hold:

- pattern compiles in the timeout-capable engine.
- static nested-quantifier/unsafe construct guard passes.
- match application completes under timeout.
- non-empty keys are produced without duplicate explosion.
- comments/decoys/negative fixtures do not overmatch.
- rule does not enumerate expected literal keys.
- consensus threshold passes.

Rejected skills are never applied or persisted. Accepted skills are written atomically and a reload test proves
the next run performs zero LLM calls.
