# Provider Freshness Validation

Validated on 2026-03-30 with:

- direct `httpx`
- local FlareSolverr `http://127.0.0.1:8191/v1`
- probe script: `scripts/provider_probe.py`

## 1. Why this matters

A provider page being reachable is not enough.

For a usable SMS pool, the key question is:

> How recently did this number receive a real SMS?

This validation treats numbers with no fresh SMS for several hours as low-value or unusable for default selection.

## 2. Probe method

For each provider:

1. fetch discovery page
2. sample number detail pages
3. extract latest visible message age
4. mark pages as restricted when the page is public but SMS content is hidden behind login or other gating

Main thresholds used in analysis:

- `<= 60 min`: hot
- `<= 180 min`: usable
- `<= 360 min`: weak fallback only
- `> 360 min`: stale for default selection

## 3. Sample results

### 10-sample expanded validation

| Provider | Sample | Fresh <= 60m | Fresh <= 180m | Fresh <= 360m | Verdict |
| --- | ---: | ---: | ---: | ---: | --- |
| `receive_smss` | 10 | 10 | 10 | 10 | Excellent |
| `temp_number` | 10 | 10 | 10 | 10 | Excellent |
| `sms24` | 10 | 8 | 8 | 8 | Strong, but mixed stale numbers exist |
| `smstome` | 10 | 8 | 9 | 10 | Strong |
| `receive_sms_free_cc` | 10 | 2 | 5 | 6 | Medium |
| `jiemahao` | 10 | 2 | 6 | 8 | Medium but dirty data risk |

### Additional provider outcomes

| Provider | Observed state | Verdict |
| --- | --- | --- |
| `temporary-phone-number.com` | Mixed. Some pages showed only a site-generated row saying login is required to view SMS. Others had messages, but latest SMS was often 14-20 hours old. | Do not use by default |
| `freephonenum.com` | Discovery works through FlareSolverr, but sampled pages were either empty or had very old messages such as 2 years / 5 years ago. Discovery also contains `Register to View`. | Do not use by default |
| `www.receivesms.org` | Sample detail page had latest visible SMS at 5 months ago. | Do not use |

## 4. Current provider ranking for pool construction

## Tier A: default pool backbone

Use these first.

1. `receive-smss.com`
2. `temp-number.com`
3. `sms24.me`
4. `smstome.com`

Reason:

- strong recent-message yield
- public pages are actually readable
- discovery is practical

## Tier B: fallback providers

Use only after freshness filtering.

1. `receive-sms-free.cc`
2. `jiemahao.com`

Reason:

- some numbers are fresh
- a large share are already stale
- page noise / anti-bot text / dirty data require stricter parsing and filtering

## Tier C: exclude from default selection

1. `temporary-phone-number.com`
2. `freephonenum.com`
3. `www.receivesms.org`

Reason:

- login-gated or partially restricted
- many sampled numbers are stale by many hours, days, months, or years
- default random selection would produce poor pool quality

## 5. Validated freshness policy

## Number-level policy

Each number should maintain:

- `last_message_at`
- `last_message_age_min`
- `freshness_bucket`
- `restricted`
- `restricted_reason`

### Recommended bucket mapping

- `hot`: `<= 60 min`
- `warm`: `61..180 min`
- `cooling`: `181..360 min`
- `stale`: `> 360 min`
- `restricted`: page can be fetched but SMS content is not really public

### Default selection rule

Only select:

- `restricted = false`
- `last_message_age_min <= 180`

Fallback when the user forces a narrow country/provider:

- allow `181..360`

Default exclude:

- `> 360`
- no visible SMS age
- login-gated / register-to-view pages

## 6. Interpretation of the user rule

The user suspicion was:

> If a number has not received a new SMS for several hours, it may be unusable.

Current validation supports that.

Evidence:

- the best providers are dominated by numbers with latest SMS within 1 hour
- mixed providers such as `sms24`, `receive-sms-free.cc`, and `jiemahao` contain a visible split between very fresh numbers and clearly stale numbers
- stale providers show ages like 10 hours, 20 hours, days, months, or years

Therefore the practical rule should be:

- `> 3 hours`: stop treating the number as a preferred candidate
- `> 6 hours`: do not auto-select by default
- `> 24 hours`: mark stale and remove from active pool

## 7. Provider-specific handling rules

### `receive-smss.com`

- safe default provider
- current sampled numbers were all recent
- good baseline for initial pool quality

### `temp-number.com`

- best current freshness observed
- should get high provider priority

### `sms24.me`

- keep in top tier
- number-level freshness filter is mandatory because some numbers were fresh and some were 9-10 hours stale on the same country page

### `smstome.com`

- strong candidate
- sampled numbers clustered around 1 hour freshness

### `receive-sms-free.cc`

- usable only with strict freshness filter
- do not assume provider-level quality means every number is good

### `jiemahao.com`

- keep lower trust
- filter out placeholder and dirty rows aggressively
- only keep numbers whose latest SMS is recent

### `temporary-phone-number.com`

- must detect login-required placeholder rows
- a page can look fresh because the site itself posted a `just now` restriction row
- such numbers must be excluded

### `freephonenum.com`

- do not promote into active pool unless a sampled detail page proves recent real SMS
- `Register to View` and very old histories make the current pool weak

## 8. Recommended data-model additions

Add these fields if not already present:

- `numbers.last_message_age_min`
- `numbers.freshness_bucket`
- `number_sources.restricted`
- `number_sources.restricted_reason`
- `number_sources.last_real_message_at`
- `number_sources.last_real_message_age_min`

## 9. Recommended scheduler behavior

### Discovery

- Tier A: every 10-15 minutes
- Tier B: every 20-30 minutes
- Tier C: manual or very low frequency

### Reclassification

For any number in the active pool:

- if latest age exceeds 180 minutes, demote from preferred pool
- if latest age exceeds 360 minutes, remove from default selection
- if latest age exceeds 1440 minutes, mark stale

## 10. Immediate build recommendation

If implementation starts now, build the default active pool from:

- `receive-smss.com`
- `temp-number.com`
- `sms24.me`
- `smstome.com`

Then add filtered fallback support for:

- `receive-sms-free.cc`
- `jiemahao.com`

Do not put these into the default random pool yet:

- `temporary-phone-number.com`
- `freephonenum.com`
- `www.receivesms.org`
