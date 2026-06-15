# Decision: Rate Parser Scope — Hourly Rates, Annual Rejection, Umbrella IR35

**By:** Ada (Data Extraction)
**Date:** 2026-06-15
**Status:** Proposed
**Scope:** `src/mechpm/extractor/rate_parser.py` — new module

---

## Context

The v0.2 pipeline stored 44 listings with only 1 having `day_rate_min` populated.
Rate information was present in descriptions but not parsed because `regex_fields.parse_rate`
required a leading £ symbol. This decision records the scope choices made in the rate_parser
implementation for team awareness.

---

## Decision 1: Hourly rates stored as-is; no conversion to daily

**Chose:** `rate_period='hour'`, raw hourly value in `day_rate_min` (e.g. 46.30ph → min=46, period=hour)

**Rejected alternative:** Multiply by 8 to derive a daily equivalent

**Reasoning:** Converting hourly to daily introduces a policy assumption (8-hour day, 
5-day week) that is not always true for contract roles. Some are 4-day weeks or 
10-hour shifts. Preserving source truth (`period='hour'`) lets Polly display 
"£46/hr" directly. If daily equivalent is ever needed, it should be a display 
concern — not a storage mutation. Arthur's acceptance threshold covers this field.

---

## Decision 2: Annual salaries are rejected (no day-rate estimation)

**Chose:** If an amount is accompanied by "per annum", "per year", "annually", or "pa"
within 40 characters, the amount is skipped entirely. `day_rate_*` fields remain None.

**Rejected alternative:** Apply a working-days divisor (e.g. £60k ÷ 220 = £273/day)

**Reasoning:** The task spec explicitly states "For annual salaries like '£60k-£70k': 
do NOT populate day_rate_* fields (they aren't day rates)." Beyond spec compliance:
FTC roles (fixed-term contracts counted as 'contract') often quote annual equivalents
genuinely. Applying a divisor to an FTC annual salary and showing it as a day rate
would be a precision-loss that could mislead filtering (e.g. flagging a £273 "day rate"
as below the Polly sanity threshold of £250).

**Impact:** `Up to 40,000 Per Annum` (Macildowie PM) correctly shows Rate TBC. 
This is the right outcome.

---

## Decision 3: 'umbrella' added as a valid ir35_status value

**Chose:** `_extract_ir35()` in rate_parser returns `"umbrella"` when the word
"umbrella" appears anywhere in the description and no explicit inside/outside IR35
phrase is present.

**Note for Arthur:** The existing schema already defines `ir35_status` as
`"inside" / "outside" / "umbrella" / None` — this is not a schema change.
However, the v0.1 `parse_ir35` in `regex_fields.py` never returned "umbrella".
The rate_parser module now does. **Arthur should update any test assertions that
expected `ir35_status=None` for umbrella-mentioning descriptions.**

**Impact:** 8 new umbrella detections in the 44-listing dataset. Examples:
- Assistant PM (York/Manchester): `Rate: 321 per day Umbrella Contract`
- PMO Co-ordinator: `41.50/hr umbrella rate`
- HR Transformation PM: `36.45 per hour umbrella`

---

## Decision 4: `ph\b` not `\bph\b` for per-hour shorthand

**Technical:** When `ph` immediately follows a digit (e.g. `46.30ph`), there is no
`\w → \W` word boundary before `p` because digits are `\w` characters. The correct
pattern uses a trailing-only word boundary: `ph\b`. This matches `46.30ph`, `45ph`,
`38-48ph` correctly.

**Note for Arthur:** The existing `regex_fields.py` RATE_SINGLE_RE and RATE_RANGE_RE
also missed `ph`-shorthand rates because those patterns required `£` before the number.
This is documented but not changed (backward compatibility with existing tests).

---

## Implications

- Polly: `rate_period='hour'` listings should display as "£X/hr" not "£X/day".
  Confirm that the reporter handles both periods correctly.
- Arthur: Gold set may need `ir35_status='umbrella'` assertions in edge cases that
  mention umbrella but not inside/outside IR35.
- Michael: Adapters that provide day rates in `salary_raw` (without `salary_annualised=True`
  in metadata) will have those values regex-parsed by rate_parser. This is correct behaviour.
