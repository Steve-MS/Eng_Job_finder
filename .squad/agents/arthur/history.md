# Arthur — History

## Project Seed (2026-06-12)
- **Project:** Mech-PM-Finder
- **Owner:** Steve
- **My role:** Tester / QA Reviewer. I gate extraction accuracy, dedup quality, and false-positive rates.
- **Mission:** Make sure the report only contains contract PM roles in UK mechanical engineering — no perm, no non-UK, no unrelated PM domains — and that day-rate / IR35 / date fields are reliable.

## Learnings

### 2026-06-12: MVP Acceptance Criteria (v0.1)
**Headline Thresholds:**
- Per-source adapter: ≥10 listings | graceful failure on HTTP 5xx | robots.txt compliance | schema-change alerts
- Extraction accuracy: 95% precision/recall on structured fields (URL, contract_type); 80% on semi-structured (day_rate, start_date); 75% on fuzzy (location)
- Contract filter: 98% precision (no perms slip through), 92% recall
- UK filter: 99% precision (no non-UK), 95% recall  
- Domain filter (mechanical): 96% precision, 90% recall
- PM filter: 92% precision, 88% recall
- Dedup: 98% precision (no false merges), 88% recall (acceptable missed duplicates)
- E2E pipeline: ≤15 min wall-clock; graceful partial failure; data persistence (raw, extracted, dedup'd)
- Report quality: 99% URL integrity, 100% no duplicates, outliers flagged

**Test Categories I'll Maintain:**
1. Gold sets: extraction (50+ listings), dedup (100+ pairs), domain + source coverage
2. Confusion matrices per field (precision/recall/F1 per Ada output)
3. Filter classification metrics per source (contract, UK, PM, mechanical)
4. Dedup merge audit log (every merge traceable)
5. E2E smoke test (mock source failure; verify graceful continuation)
6. Report hygiene checks (URL validity, duplicate rows, outlier flags, metadata completeness)
7. Per-source adapter regression test suite (once merged, stays passing)

**Asymmetry Rules (Precision > Recall):**
- False positives (perm, non-UK, non-mechanical, non-PM) are worse than false negatives
- Contract precision 98% vs recall 92% (better to miss a contract than include a perm)
- UK precision 99% vs recall 95% (better to miss a UK role than include non-UK)
- Domain precision 96% vs recall 90% (better to miss mechanical than include non-mechanical)
- PM precision 92% vs recall 88% (medium bar; some role-title ambiguity acceptable)

---

## 2026-06-12: MVP Plan Fan-Out Complete
**Team Sync:** MVP plan fan-out completed on 2026-06-12T16:30Z. All agents delivered decisions. Inbox files merged into `.squad/decisions.md` (the authoritative ledger). Orchestration logs written. Next cycle: build gold-set harness and acceptance-gating tests per spec. See `.squad/decisions.md` for consolidated architecture, sources, schema, report format, and acceptance criteria. All team members synchronized; design is locked in.
