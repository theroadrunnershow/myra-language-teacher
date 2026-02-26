# Security Hardening & DDoS Resilience Review
**Date:** 2026-02-25 | **Reviewer:** Claude (claude-sonnet-4-6) | **Branch:** claude/security-hardening-ddos-QrNUV

---

## Executive Summary (Non-Technical)

Myra Language Teacher is a low-risk, public-facing family app with no user accounts, no payments, and no sensitive data. The biggest real-world threats are **cost-based attacks** (someone hammering the speech recognition endpoint to run up a Google Cloud bill or exhaust gTTS API limits) and **availability disruption** (forcing the single Cloud Run instance into OOM or CPU saturation).

The current posture is **better than average** for a hobby app — Cloud Armor WAF with per-IP rate limits is already deployed. But five concrete gaps need closing, two of which are critical (direct Cloud Run URL bypass of WAF; no audio size cap). All issues are fixable in a single pull request with no external service changes needed.

**Risk level before fixes: Medium**
**Risk level after fixes: Low**

---

## Threat Model (STRIDE + Abuse Cases)

### Assets
| Asset | Sensitivity | Impact if compromised |
|---|---|---|
| Google Cloud billing account | High | Financial — run up $$$+ charges |
| Cloud Run instance RAM/CPU | Medium | Availability — OOM crash, slow responses |
| gTTS API quota | Medium | Availability — TTS fails for Myra |
| Whisper model in memory | Low | Availability — replaced on crash |
| Static word database | None | Public data, read-only |
| User session data | None | Stored in browser sessionStorage only |

### Trust Boundaries
```
Internet → Cloud Armor WAF → Global LB → [CDN cache hit]
                                       → Cloud Run (origin)
Internet → Cloud Run *.run.app URL (BYPASSES Cloud Armor) ← CRITICAL GAP
```

### High-Risk Flows
| Flow | Threat | Current Control |
|---|---|---|
| POST /api/recognize + large audio file | OOM crash, cost amplification via Whisper | None (no size limit) |
| GET /api/tts?text=<10k chars> | gTTS quota burn, slow response | None (no length limit) |
| Direct *.run.app URL calls | Bypasses Cloud Armor rate limits entirely | None |
| Rapid-fire /api/recognize from single IP | Whisper CPU saturation | Cloud Armor 10/min (but only via LB path) |
| /api/dino-voice endpoint | Same cost as /api/tts, unprotected | No Cloud Armor rule |
| Budget kill-switch compromise | Attacker scales Cloud Run to 0 | SA has project-wide run.admin |

### Abuse Cases
1. **Cost-amplification**: Attacker hits `/api/recognize` via the direct `*.run.app` URL at full speed, bypassing Cloud Armor. Uploads 100 MB audio files → Whisper OOM → Cloud Run crash loop → billing spike.
2. **TTS abuse**: Flood `/api/tts` with 10,000-character texts → gTTS makes expensive API calls, slow responses back up the thread pool.
3. **DDoS via slow clients**: Send audio upload slowly, holding a Cloud Run connection open for the full 300s timeout window. With max=2 instances, just 2 simultaneous slow uploads kill availability.
4. **Scanner noise**: Automated vulnerability scanners triggering 5xx errors and filling logs, making real issues harder to spot.

---

## Findings (Technical)

### CRITICAL

#### FIND-01: Cloud Armor WAF completely bypassable via direct Cloud Run URL
- **Location**: `infra/cloud_run.tf:68-74`
- **Detail**: `google_cloud_run_v2_service_iam_member.public` grants `allUsers` direct invocation access to the Cloud Run service. Every Cloud Run service gets a public `*.run.app` URL. Traffic hitting this URL skips Cloud Armor entirely — all rate limits, OWASP rules, and DDoS protections are moot.
- **Fix**: Set `ingress = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"` on the Cloud Run service. This forces all external traffic through the load balancer (and thus Cloud Armor) while keeping the LB path functional.

#### FIND-02: No audio file size limit — OOM crash vector
- **Location**: `main.py:141`
- **Detail**: `audio_data = await audio.read()` reads the entire upload into RAM with no size check. Cloud Run instances have 3 GB RAM. A 2 GB upload will OOM-crash the instance. At max=2 instances, two simultaneous large uploads cause total service unavailability.
- **Fix**: Reject files > 10 MB before reading (checked via `Content-Length` header early, then verified after reading).

### HIGH

#### FIND-03: No text length limit on TTS endpoints — quota burn + slow drain
- **Location**: `main.py:98`, `main.py:113`
- **Detail**: `/api/tts` and `/api/dino-voice` accept arbitrary-length `text` query params. gTTS sends the entire string to Google's TTS API. Very long strings delay the thread pool, slow responses, and could exhaust Google TTS quota.
- **Fix**: Cap `text` at 200 characters (generous for any word a 4-year-old learns).

#### FIND-04: `/api/dino-voice` has no Cloud Armor rate limit
- **Location**: `infra/cloud_armor.tf`
- **Detail**: Cloud Armor has rules for `/api/recognize` and `/api/tts` but not `/api/dino-voice`, which makes the same expensive gTTS network call. An attacker can hammer this endpoint without triggering a rate-limit ban.
- **Fix**: Add a 30 req/min rate limit rule for `/api/dino-voice` (same budget as TTS).

#### FIND-05: Kill-switch service account has project-wide `roles/run.admin`
- **Location**: `infra/budgets.tf:87-91`
- **Detail**: The Cloud Function that scales the app to zero is granted `roles/run.admin` at the **project** level. If the kill-switch function is compromised (e.g., via a supply-chain attack on its dependencies), an attacker could create, delete, or modify **any** Cloud Run service in the project.
- **Fix**: Replace the project-level IAM binding with a service-level `google_cloud_run_v2_service_iam_member` scoped to just `dino-app`. This follows least-privilege and reduces blast radius.

### MEDIUM

#### FIND-06: No security response headers — XSS and clickjacking exposure
- **Location**: `main.py` (no middleware)
- **Detail**: Responses include no `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, or `Strict-Transport-Security`. The Jinja2 templates contain inline JS (legitimate), but without CSP a stored/reflected XSS finding (future) would have no mitigating header.
- **Fix**: Add a `SecurityHeadersMiddleware` to FastAPI. Since there's no user auth or PII, XSS risk is low — but headers are zero-cost to add.

#### FIND-07: No OWASP preconfigured rules in Cloud Armor
- **Location**: `infra/cloud_armor.tf`
- **Detail**: Cloud Armor has rate-limit rules but no signature-based OWASP protection (XSS patterns in query params, SQLi, LFI/RFI, scanner fingerprints). These rules add defense-in-depth against automated scanners.
- **Fix**: Add `evaluatePreconfiguredExpr` rules for XSS, SQLi, LFI, RFI, and scanner detection at priority 900 (before rate limits).

#### FIND-08: `similarity_threshold` fully client-controlled, no server clamp
- **Location**: `main.py:139`
- **Detail**: The similarity threshold is sent as a form field from the browser and accepted verbatim. A client can set `similarity_threshold=0` to mark every attempt as correct. This is low severity (no auth, no rewards), but it undermines the educational intent.
- **Fix**: Clamp `similarity_threshold` server-side to `[0, 100]` and validate it's a real float.

#### FIND-09: `language` parameter not validated against allowlist
- **Location**: `main.py:98`, `main.py:131`
- **Detail**: The `language` form/query parameter is used to look up a Whisper language code and passed to gTTS. Unknown languages fall back silently (`lang_code = LANGUAGE_CODES.get(language, "te")`). In TTS, an invalid language code passed to gTTS could cause an unexpected error or future SSRF-adjacent risk if the language code space expands.
- **Fix**: Validate `language in VALID_LANGUAGES` and return 400 if not.

### LOW

#### FIND-10: No dedicated health check endpoint — probes render full Jinja2 page
- **Location**: `infra/cloud_run.tf:41-49`
- **Detail**: Startup and liveness probes hit `/` which renders a full Jinja2 HTML page (reading config, templating). This is unnecessary overhead for every 10–30 second probe cycle.
- **Fix**: Add `GET /health` → `{"status": "ok"}` and update probe paths.

#### FIND-11: CORS wildcard allows cross-origin API calls
- **Location**: `main.py:21-26`
- **Detail**: `allow_origins=["*"]` is set. Since there are no authentication cookies or tokens, this presents minimal real risk (CORS protects credentials, not public data). Documented for completeness.
- **Note**: No change recommended — restricting CORS would break legitimate browser requests. Risk is negligible with no auth state.

#### FIND-12: `/api/config` POST has no body size limit
- **Location**: `main.py:64-73`
- **Detail**: A POST with a multi-megabyte JSON body will be fully buffered by FastAPI before validation.
- **Fix**: Add a `Content-Length` check and a 4 KB body cap.

---

## Top 10 Realistic Failure Modes

| # | Failure Mode | Impact | Likelihood | Detection Signal | Mitigation |
|---|---|---|---|---|---|
| 1 | Attacker bypasses Cloud Armor via `*.run.app` URL, floods `/api/recognize` | Billing spike, OOM crash | Medium | Unusual traffic on Cloud Run logs without LB logs | Ingress restriction (FIND-01) |
| 2 | Large audio upload causes OOM crash on all instances | Full outage | Low | Cloud Run OOM errors in logs | Audio size limit (FIND-02) |
| 3 | Budget kill-switch fires during legitimate traffic spike | Outage until manually re-enabled | Low | Cloud Run scale-to-zero event | Budget tuning, alerting |
| 4 | gTTS rate limit / quota exhaustion from TTS abuse | TTS fails, degraded UX | Low-Medium | 5xx from /api/tts | Rate limit (FIND-04) + text cap (FIND-03) |
| 5 | Cold-start cascade: burst of requests during scale-up | Slow responses, queuing | Medium | p95 latency spike, 503s | Cloud Armor rate limit existing |
| 6 | Kill-switch function supply-chain compromise | All Cloud Run services at risk | Very Low | Cloud audit logs | Least-privilege IAM (FIND-05) |
| 7 | Whisper temp files not cleaned on crash | Disk pressure (tmp fills up) | Very Low | Disk usage logs | Already has `finally: os.unlink()` — OK |
| 8 | ffmpeg vulnerability via malformed audio bytes | RCE potential in container | Very Low | Container crash, unexpected logs | Rely on GCP sandboxing + container isolation |
| 9 | Scanner noise fills Cloud Logging, hides real events | Alert fatigue | Medium | High 4xx rate from known scanner IPs | OWASP scanner detection (FIND-07) |
| 10 | Session config tampering via `/api/config` | Low (no server state) | Low | Unusual config values in request logs | Input validation (existing + FIND-12) |

---

## Prioritized Remediation Roadmap

| Fix | FIND | Risk Reduced | Effort | Owner | Timeline |
|---|---|---|---|---|---|
| Cloud Run ingress restriction | 01 | Critical — WAF bypass eliminated | S | Infra | **Day 1** |
| Audio upload size limit (10 MB) | 02 | High — OOM crash vector closed | S | Backend | **Day 1** |
| TTS text length cap (200 chars) | 03 | High — quota burn closed | S | Backend | **Day 1** |
| `/api/dino-voice` Cloud Armor rule | 04 | High — rate limit gap closed | S | Infra | **Day 1** |
| Kill-switch least-privilege IAM | 05 | High — blast radius reduction | S | Infra | **Day 1** |
| Security response headers | 06 | Medium — XSS/clickjack hardening | S | Backend | **Day 1** |
| OWASP preconfigured Cloud Armor rules | 07 | Medium — scanner/injection defense | S | Infra | **Day 1** |
| Language input validation + allowlist | 09 | Medium — input hygiene | S | Backend | **Day 1** |
| similarity_threshold server-side clamp | 08 | Low — educational integrity | S | Backend | **Day 1** |
| Dedicated /health endpoint | 10 | Low — probe efficiency | S | Backend | **Day 1** |
| /api/config body size limit | 12 | Low — resource protection | S | Backend | **Day 1** |
| **Foundational**: Cloud Armor geo-restriction | — | Medium (if regional use only) | M | Infra | Week 2 |
| **Foundational**: Structured/redacted logging | — | Medium — audit quality | M | Backend | Week 2 |
| **Foundational**: Load test & chaos validation | — | Confidence | M | QA | Week 3 |

---

## DDoS Runbook

### Detection
- **Signal 1**: Cloud Armor > 5 rate-limit `deny(429)` events/minute from single IP range → automated attack
- **Signal 2**: Cloud Run request count > 20 concurrent requests → saturation (max=2 instances, ~10/instance)
- **Signal 3**: Cloud Billing alert at 80% budget ($40) → cost spike in progress
- **Signal 4**: Error rate > 5% on `/api/recognize` or `/api/tts` → likely abuse or saturation

### Triage (5 minutes)
1. Check Cloud Armor logs: `resource.type="http_load_balancer"` in Cloud Logging
2. Identify source: single IP? ASN? User-Agent pattern?
3. Check Cloud Run metrics: instance count, request latency, CPU usage
4. Check billing: is current spend tracking toward kill-switch threshold?

### Mitigations (in order of aggression)
1. **If single IP**: Cloud Armor already auto-bans rate violators. Verify ban duration (120s). Manually add a permanent deny rule if still attacking after multiple ban cycles.
2. **If IP range / ASN**: Add a Cloud Armor rule `inIpRange(origin.ip, 'x.x.x.x/24')` at priority 500 (before rate limits).
3. **If distributed / botnet**: Enable Cloud Armor threat intelligence (`evaluateThreatIntelligence('iplist-tor-exit-nodes')` etc.) — requires Cloud Armor Managed Protection Plus ($3K/month). Alternative: temporarily lower rate limits to 2/min on `/api/recognize`.
4. **If budget at risk**: Manually invoke kill-switch Cloud Function early, or set `max_instances=0` in Cloud Run directly.
5. **Emergency**: Update Cloud Armor default rule from `allow` to `deny(403)` temporarily, then add an allowlist for known good IPs.

### Rollback
1. Remove any manually added Cloud Armor deny rules
2. Restore `max_instances` to 2
3. Verify liveness probe passes
4. Run smoke test: `curl https://<domain>/health`

### Comms Template
> "Myra Language Teacher experienced a service disruption between [TIME] and [TIME] due to unexpected traffic volume. The service automatically recovered / was manually restored. No user data was affected (the app stores no personal information). We have additional protections in place to prevent recurrence."

---

## Validation Plan

### Load Test
```bash
# Tool: k6 or hey
# Scenario 1: normal usage (should pass)
hey -n 100 -c 5 -q 1 https://<domain>/api/word
# Expected: all 200s, p99 < 500ms

# Scenario 2: rate limit enforcement (should trigger 429)
hey -n 200 -c 20 https://<domain>/api/word
# Expected: 429s after 100 req/min threshold

# Scenario 3: /api/recognize rate limit
hey -n 30 -c 5 -m POST https://<domain>/api/recognize
# Expected: 429s after 10 req/min
```

### Security Scanning
```bash
# OWASP ZAP baseline scan (passive, safe)
docker run -t owasp/zap2docker-stable zap-baseline.py -t https://<domain>
# Expected: no high-severity findings

# Nikto scanner (active, lightweight)
nikto -h https://<domain> -Tuning 1234
# Expected: no critical findings
```

### Chaos Engineering
1. Send 10 MB audio file to `/api/recognize` → expect HTTP 413
2. Send 500-char text to `/api/tts` → expect HTTP 400
3. Hit `*.run.app` URL directly → expect HTTP 403 (ingress restriction)
4. Send `similarity_threshold=200` → expect HTTP 400
5. Send `language=malicious` → expect HTTP 400

### Success Criteria
- [ ] `GET /health` returns 200 in < 50ms
- [ ] `POST /api/recognize` with 11 MB body returns 413
- [ ] `GET /api/tts?text=<201 chars>` returns 400
- [ ] Direct `*.run.app` URL returns 403 Forbidden
- [ ] `X-Content-Type-Options: nosniff` present on all responses
- [ ] `X-Frame-Options: DENY` present on all responses
- [ ] Cloud Armor logs show `deny(429)` on rate limit violation
- [ ] `/api/dino-voice` triggers 429 after 30 req/min
- [ ] All existing tests still pass
