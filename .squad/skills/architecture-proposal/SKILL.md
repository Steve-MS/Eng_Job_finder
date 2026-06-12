# Skill: Architecture Proposal

**Owner:** Tommy (Lead)  
**Created:** 2026-06-12

## When to Use
When the team needs a new system or major subsystem designed from scratch. Triggers: "propose architecture", "design the system", "tech stack decision".

## Steps

1. **Read context** — history.md, decisions.md, any prior art.
2. **Tech stack table** — one row per layer (language, storage, scheduler, LLM, report format, HTTP client, config). Each row: choice + 1-2 line justification. Optimize for the stated constraints.
3. **Pipeline diagram** — ASCII or Mermaid showing data flow from trigger to output. Label each module with its owning agent.
4. **Contract definition** — define the interface that plug-in modules must implement. Include: method signatures, data shapes, error semantics, throttling, compliance notes.
5. **Repo layout** — folder tree with comments showing ownership.
6. **Scope cut** — IN (ship now) vs DEFERRED (later). Be ruthless. Recommend the smallest slice that proves the pipeline end-to-end.
7. **Write outputs** — decision drop file to `.squad/decisions/inbox/`, update history.md with learnings.

## Quality Checks
- Every choice has a "why" (not just "what").
- MVP is achievable in ≤3 iterations by the team.
- No single point of failure in the pipeline (one bad adapter can't crash the run).
- Contract is testable (Arthur can write fixtures against it).
