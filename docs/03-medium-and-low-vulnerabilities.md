# 🟡 Medium & 🟢 Low Vulnerabilities

Real weaknesses, but either lower-likelihood, lower-impact, or defence-in-depth. Schedule them right after the criticals/highs. Several are quick wins.

---

## 🟡 Medium

### SEC-09 — `paper_preview` disables clickjacking protection for everyone

**Type:** Clickjacking / Framing Policy · **Severity:** 🟡 Medium
**Location:** [home/views.py:658-680](home/views.py#L658-L680)

```python
@ratelimit(...)
@xframe_options_exempt          # <-- removes X-Frame-Options on this response
def paper_preview(request, paper_id, paper_title=None):
```

**What it is.** Django defaults to `X-Frame-Options: DENY` (via `XFrameOptionsMiddleware`), which stops other sites from embedding your pages in an `<iframe>`. You explicitly *exempt* the preview endpoint — almost certainly because your own search/profile thumbnails embed it in iframes ([search.html:678](home/templates/home/search.html#L678), [profile.html:817](home/templates/home/profile.html#L817)). But `@xframe_options_exempt` removes the header for **all** origins, not just yours.

**Why it's a risk.** Any malicious website can now frame `…/preview/` and overlay it for clickjacking, or embed users' document content on a hostile page. It's most sensitive for owner-only/pending previews.

**How it's exploited.** Attacker builds a page that iframes your preview URL, positions a transparent "Claim your prize" button over it, and tricks a logged-in user into interacting with framed content — or simply embeds private-ish documents on their own site.

**The fix.** You only need *same-origin* framing, so don't fully exempt — restrict instead:
```python
from django.views.decorators.clickjacking import xframe_options_sameorigin

@xframe_options_sameorigin
def paper_preview(...):
    ...
```
Better still, add a CSP `frame-ancestors 'self'` (see [SEC-15](docs/03-medium-and-low-vulnerabilities.md#sec-15--no-content-security-policy-inline-scripts--un-pinned-cdn-assets)) which supersedes `X-Frame-Options` in modern browsers.

---

### SEC-10 — MIME validation "fails open" when `python-magic` is absent

**Type:** File Upload Validation · **Severity:** 🟡 Medium
**Location:** [home/views.py:32-35](home/views.py#L32-L35) and [home/views.py:312-320](home/views.py#L312-L320)

```python
try:
    import magic
except ImportError:
    magic = None
...
def _detect_uploaded_file_mime(uploaded_file):
    initial_bytes = uploaded_file.read(2048)
    uploaded_file.seek(0)
    if magic is not None:
        return magic.from_buffer(initial_bytes, mime=True)   # strong: reads file contents
    detected_mime, _ = mimetypes.guess_type(uploaded_file.name)  # weak: trusts the extension
    return detected_mime
```

**What it is.** Your strong validation (inspecting the actual bytes with libmagic) silently downgrades to **extension-only guessing** if `python-magic`/libmagic isn't installed — which is common, especially on Windows and minimal container images. So the "binary inspection" you wrote may never actually run.

**Why it's a risk.** Extension-based checking trusts the attacker-controlled filename. A file named `paper.pdf` containing arbitrary bytes passes. (You're partly saved because previews are served with an extension-derived `Content-Type`, so an HTML payload renamed `.pdf` is served as `application/pdf` and won't execute — good. But "fail open" still weakens a control you clearly intended to be strong, and it's invisible: nothing tells you it degraded.)

**The fix.** Fail **closed** — if you can't verify content, reject:
```python
if magic is None:
    # don't silently trust the extension; refuse to accept uploads we can't inspect
    raise RuntimeError("python-magic is required for upload validation")
```
And make `python-magic` (plus the libmagic binary) a **hard, pinned dependency** in `requirements.txt`, verified at startup. A control that only works "if a library happens to be present" isn't a control.

---

### SEC-11 — No aggregate upload cap; orphaned files on partial failure (I DONT KNOW maybe I did it alraedy!!!!!!)

**Type:** Resource Abuse / Cleanup · **Severity:** 🟡 Medium
**Location:** [home/views.py:761-812](home/views.py#L761-L812)

**What it is.** Each file is validated against a 10 MB per-file limit, and up to 3 images are allowed — but there's **no cap on the total request size**, and files are written to storage *inside* the transaction before the request fully completes. If the transaction rolls back after some `default_storage.save(...)` calls (the extra images are saved at [views.py:802-805](home/views.py#L802-L805), outside the DB row), those bytes are left behind.

**Why it's a risk.** 3 × 10 MB = 30 MB per request, repeatable at 5/min (your rate limit), per user — that's a slow storage-filling / bandwidth abuse. And failed uploads leave orphaned files in `vault/pending/` that nothing ever cleans up (you can see leftover orphans in the folder already).

**The fix.**
- Enforce a total-request cap (sum of file sizes) before saving anything.
- Save files **after** the DB row is safely created, or register an `transaction.on_commit` to finalise storage and an explicit cleanup (`default_storage.delete`) on the failure path.
- Add a periodic job that deletes `vault/pending/` files with no matching `Record` older than N hours.

---

### SEC-16 — Report feature is spammable and grows an unbounded text column

**Type:** Abuse / Data Model · **Severity:** 🟡 Medium
**Location:** [home/views.py:591-626](home/views.py#L591-L626), backed by [models.py:70](home/models.py#L70) (`msg = models.TextField`)

**What it is.** Every report appends a line to `record.msg` (a single free-text column). Dedup is per exact `"<reason> - reported by <username>"` string, so each user can still add up to 6 distinct lines (one per reason) per paper, and there's no per-user/per-paper report limit beyond the shared 100/min IP rate limit on the whole view.

**Why it's a risk.** A handful of logged-in accounts can bloat `msg`, flood the admin "Reported Papers" dashboard with noise, and effectively run a **moderation denial-of-service** (drown real reports). Storing reports as concatenated text also makes them impossible to query, count, or resolve individually.

**The fix.**
- Move reports into their own normalised model:
  ```python
  class Report(models.Model):
      record = models.ForeignKey(Record, on_delete=models.CASCADE, related_name='reports')
      reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
      reason = models.CharField(max_length=64, choices=REPORT_CHOICES)
      created_at = models.DateTimeField(auto_now_add=True)
      class Meta:
          constraints = [models.UniqueConstraint(fields=['record','reporter','reason'], name='one_report_per_reason')]
  ```
  The `UniqueConstraint` enforces "one report per reason per user per paper" at the DB level — no string parsing, no unbounded growth.
- Rate-limit the report action per user (not just per IP).

---

### SEC-17 — Rate limiting uses per-process memory (ineffective on serverless / multi-worker)

**Type:** Abuse Controls / Misconfiguration · **Severity:** 🟡 Medium
**Location:** `@ratelimit(...)` throughout [views.py](home/views.py) and [urls.py:11-13](UniPapers/urls.py#L11-L13); no `CACHES` defined in [settings.py](UniPapers/settings.py)

**What it is.** `django-ratelimit` stores counters in Django's cache. You haven't configured `CACHES`, so Django defaults to **`LocMemCache`** — a separate in-memory dict *per process*. On Vercel (serverless: many short-lived isolated invocations) or any multi-worker setup, each process has its own counters, and they reset constantly.

**Why it's a risk.** Your `5/m` upload limit, `3/m` login limit, etc. are effectively "5 per minute *per worker process*." With N workers (or N cold serverless invocations) the real limit is N× higher and unpredictable — so the brute-force / abuse protection you wrote mostly doesn't apply in production.

**The fix.** Point the cache (and the rate limiter) at a **shared backend** — Redis is the standard:
```python
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.redis.RedisCache',
                      'LOCATION': os.environ['REDIS_URL']}}
RATELIMIT_USE_CACHE = 'default'
```
Also key sensitive limits on the **user**, not just IP (`key='user'`), since many students legitimately share a campus IP/NAT — IP-only limits will both under-protect (shared attacker pool) and over-block (whole campus throttled).

> **Verify-this note:** the pattern `ratelimit(...)(include('allauth.urls'))` in [urls.py:11](UniPapers/urls.py#L11) and [urls.py:13](UniPapers/urls.py#L13) applies a *view decorator* to the result of `include()`, which is not a view. It's an unusual construction — confirm allauth routes still resolve and that the limit actually fires there. Prefer rate-limiting allauth's specific login/signup views (or putting a limit at the edge/WSGI layer) over wrapping the whole include.

---

## 🟢 Low

### SEC-12 — Ownership keyed on a mutable email string, not a user foreign key (NO cuz if they delete their account, then Signin again they lose papers)

**Type:** Access Control / Data Model · **Severity:** 🟢 Low (fragile design more than active vuln)
**Location:** [views.py:575](home/views.py#L575), [views.py:668](home/views.py#L668), [views.py:845](home/views.py#L845), [views.py:910](home/views.py#L910); model field [models.py:67](home/models.py#L67)

**What it is.** "Is this my paper?" is decided by `record.uploaded_email == request.user.email`. Ownership is a string comparison on an email, not a foreign key to the user.

**Why it's a risk.** Emails are mutable identity data. If a user's email ever changes (account merge, provider change, a future migration off Google-only), their ownership of past uploads silently breaks — they lose access to their own papers, or, worse, a *new* user who later gets that email address inherits them. It also means a `NULL`/blank email edge case could match unintended rows.

**The fix.** Add `owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)` and compare `record.owner_id == request.user.id`. Keep `uploaded_by`/`uploaded_email` only as display snapshots.

---

### SEC-13 — OAuth access tokens stored in the database for no reason

**Type:** Secrets / Data Minimisation · **Severity:** 🟢 Low
**Location:** [settings.py:86](UniPapers/settings.py#L86) — `SOCIALACCOUNT_STORE_TOKENS = True`

**What it is.** You persist Google OAuth access/refresh tokens. But you only use Google for *login* — you never call Google APIs on the user's behalf afterwards.

**Why it's a risk.** Storing tokens you don't use increases the blast radius of any DB compromise: a leaked DB now also leaks live Google tokens. Data you don't store can't be stolen.

**The fix.** Set `SOCIALACCOUNT_STORE_TOKENS = False`. (Also relevant to [SEC-03](docs/01-critical-vulnerabilities.md), where the DB is directly downloadable.)

---

### SEC-14 — GET-based logout/login enable CSRF on auth actions

**Type:** CSRF / Session · **Severity:** 🟢 Low
**Location:** [settings.py:76](UniPapers/settings.py#L76) `ACCOUNT_LOGOUT_ON_GET = True`, [settings.py:78](UniPapers/settings.py#L78) `SOCIALACCOUNT_LOGIN_ON_GET = True`

**What it is.** Logout (and social-login initiation) can be triggered by a plain GET, which carries no CSRF protection.

**Why it's a risk.** An attacker page can include `<img src="https://yoursite/accounts/logout/">` to forcibly log a victim out (annoyance / session-fixation setup), or auto-initiate a login flow. Low impact, but it's free to close.

**The fix.** Set `ACCOUNT_LOGOUT_ON_GET = False` and log out via the POST form you already have in [base.html:586-591](home/templates/home/base.html#L586-L591). `SOCIALACCOUNT_LOGIN_ON_GET = True` is a usability trade-off — acceptable, but be aware it allows login-CSRF; pairing it with PKCE (which you have ✅) mitigates the worst of it.

---

### SEC-15 — No Content-Security-Policy; inline scripts & un-pinned CDN assets

**Type:** Defense-in-Depth / Supply Chain · **Severity:** 🟢 Low
**Location:** [base.html:2-3](home/templates/home/base.html#L2-L3) (Google Fonts + Bootstrap from CDN), [base.html:649](home/templates/home/base.html#L649) (Bootstrap JS from CDN), inline `<script>`/`<style>` across templates

**What it is.** There's no CSP header, lots of inline scripts/styles, and third-party CDN assets loaded without **Subresource Integrity (SRI)** hashes.

**Why it's a risk.**
- No CSP means that *if* an XSS ever slips in (today you're well-protected by Django auto-escaping, but defence-in-depth matters), there's no second line of defence to stop injected scripts.
- A compromised/poisoned CDN response, or an attacker who can MITM the CDN fetch, can inject script into *every* page — without SRI the browser has no way to detect the swap.

**The fix.**
- Add a CSP (via `django-csp` or a middleware). A starter: `default-src 'self'; frame-ancestors 'self'; img-src 'self' https://res.cloudinary.com data:; style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; script-src 'self' https://cdn.jsdelivr.net`. Then progressively move inline scripts/styles into static files so you can drop `'unsafe-inline'`.
- Add SRI hashes to the Bootstrap `<link>`/`<script>` tags, or self-host them.

---

## A note on what's *not* here

Worth saying explicitly, because it's a credit to you:

- **No SQL injection** — every query uses the ORM safely.
- **No template XSS via `|safe`/`mark_safe`/`autoescape off`** — I grepped; you never disable escaping, and your one bit of JS data-passing uses `json_script` correctly. (One quality caveat about *double*-escaping is in [04](docs/04-code-quality-and-architecture.md), but it's a correctness bug, not a vuln.)
- **No command/shell injection, no `eval`, no `pickle` of untrusted data.**
- **No path traversal via upload filename** — you reduce to basename and rebuild safe names.

These are the bugs that end companies, and you avoided all of them. The findings above are mostly *configuration* and *architecture*, which are very fixable.

Continue to [04-code-quality-and-architecture.md](docs/04-code-quality-and-architecture.md).
