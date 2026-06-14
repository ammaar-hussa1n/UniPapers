# ✅ Remediation Roadmap

A prioritised, do-this-in-order plan. The ordering matters: it fixes the issues that *chain into each other* first, so you're never half-protected.

Each item links to its full writeup. Check them off as you go.

---

## 🚦 Launch verdict

**Not ready for public launch yet — but closer than you might think.** The blockers are concentrated in **secrets + deployment config + storage architecture**, not in your application logic (which is mostly sound). Block 1 below is a focused day or two of work and clears the genuinely dangerous stuff. Blocks 2–3 make it production-grade.

---

## Block 1 — Stop-ship blockers (do before *any* public deploy)

> These four chain into full compromise or break on launch day. Nothing else ships until they're done.

- [ ] **SEC-01** — Move `SECRET_KEY` to an env var, generate a fresh non-`insecure` key, rotate. → [details](docs/01-critical-vulnerabilities.md#sec-01--hardcoded-secret_key-and-its-the-throwaway-dev-key)
- [ ] **SEC-02** — Move the DB password/credentials to env (`DATABASE_URL`), rotate, use a least-privilege role. → [details](docs/01-critical-vulnerabilities.md#sec-02--database-password-hardcoded-in-settings)
- [ ] **SEC-03** — `DEBUG` off by default (opt-in via env); set `MEDIA_ROOT` to a dedicated folder (never `BASE_DIR`); don't serve media through Django in prod. → [details](docs/01-critical-vulnerabilities.md#sec-03--debugtrue-off-vercel--media_root--base_dir--whole-project-served-at-media)
- [ ] **SEC-06** — Replace the local-filesystem file-move on approval with a storage-agnostic approach (ideally: don't move files, just flip `status`). → [details](docs/01-critical-vulnerabilities.md#sec-06--file-approval-workflow-moves-files-on-the-local-filesystem-breaks-on-the-actual-production-target)
- [ ] **Hygiene that enables the above:** add `.gitignore`, add `requirements.txt`, delete the committed `db.sqlite3`. → [details](docs/04-code-quality-and-architecture.md#8-project-hygiene--repo-setup)

**Exit check for Block 1:**
```bash
python manage.py check --deploy      # should report no critical issues
# and manually confirm:
#   GET /media/UniPapers/settings.py   -> 404 (NOT the file)
#   GET /media/db.sqlite3              -> 404
```

---

## Block 2 — High-priority hardening (same release, before real traffic)

- [ ] **SEC-05** — Serve all files through a permission-checked view; stop exposing raw/public file URLs for pending content; use unguessable storage keys (+ signed URLs on Cloudinary/S3). → [details](docs/02-high-vulnerabilities.md#sec-05--raw-file-urls-bypass-the-approved-only-access-check-idor--enumeration)
- [ ] **SEC-04** — Add the production security-header block (secure cookies, SSL redirect, HSTS, nosniff, proxy SSL header); tighten `ALLOWED_HOSTS`. → [details](docs/02-high-vulnerabilities.md#sec-04--production-security-headers-are-all-missing-no-hsts-secure-cookies-ssl-redirect-nosniff)
- [ ] **SEC-07** — Stop overwriting files by basename; use unique storage keys / dedup-aware storage API. → [details](docs/02-high-vulnerabilities.md#sec-07--approval-overwrites-files-by-basename-osreplace--silent-data-loss)
- [ ] **SEC-08** — Stop leaking raw exception text; log server-side, show generic messages. → [details](docs/02-high-vulnerabilities.md#sec-08--raw-exception-text-leaked-to-users-on-upload-failure)

---

## Block 3 — Medium hardening (fast follow)

- [ ] **SEC-10** — Make `python-magic` a hard dependency and fail closed if MIME can't be verified. → [details](docs/03-medium-and-low-vulnerabilities.md#sec-10--mime-validation-fails-open-when-python-magic-is-absent)
- [ ] **SEC-17** — Configure a shared cache (Redis) so rate limiting actually works in prod; key sensitive limits on user. → [details](docs/03-medium-and-low-vulnerabilities.md#sec-17--rate-limiting-uses-per-process-memory-ineffective-on-serverless--multi-worker)
- [ ] **SEC-16** — Move reports into a normalised `Report` table with a uniqueness constraint; rate-limit per user. → [details](docs/03-medium-and-low-vulnerabilities.md#sec-16--report-feature-is-spammable-and-grows-an-unbounded-text-column)
- [ ] **SEC-09** — Switch `paper_preview` from `xframe_options_exempt` to `sameorigin` (or a CSP `frame-ancestors 'self'`). → [details](docs/03-medium-and-low-vulnerabilities.md#sec-09--paper_preview-disables-clickjacking-protection-for-everyone)
- [ ] **SEC-11** — Add an aggregate upload-size cap and cleanup for orphaned/partial uploads. → [details](docs/03-medium-and-low-vulnerabilities.md#sec-11--no-aggregate-upload-cap-orphaned-files-on-partial-failure)

---

## Block 4 — Low / defence-in-depth

- [ ] **SEC-12** — Add an `owner` FK; stop keying ownership on email. → [details](docs/03-medium-and-low-vulnerabilities.md#sec-12--ownership-keyed-on-a-mutable-email-string-not-a-user-foreign-key)
- [ ] **SEC-13** — `SOCIALACCOUNT_STORE_TOKENS = False`. → [details](docs/03-medium-and-low-vulnerabilities.md#sec-13--oauth-access-tokens-stored-in-the-database-for-no-reason)
- [ ] **SEC-14** — `ACCOUNT_LOGOUT_ON_GET = False`; log out via POST. → [details](docs/03-medium-and-low-vulnerabilities.md#sec-14--get-based-loginlogout-enable-csrf-on-auth-actions)
- [ ] **SEC-15** — Add a Content-Security-Policy and SRI (or self-host CDN assets). → [details](docs/03-medium-and-low-vulnerabilities.md#sec-15--no-content-security-policy-inline-scripts--un-pinned-cdn-assets)

---

## Block 5 — Code quality & architecture (before the codebase grows further)

> Best done *now*, pre-launch, while it's small. See [04-code-quality-and-architecture.md](docs/04-code-quality-and-architecture.md) for each.

- [ ] Single source of truth for the course catalog (one module or, better, DB tables) — kills the "update both files" trap.
- [ ] Normalise the data model: real `Report` table, `owner` FK, `choices`/types instead of bare `CharField`s.
- [ ] Move the approval/file workflow out of `Model.save()` into an explicit service/admin action.
- [ ] Delete dead code: `login_page`, unused `compress_image` (or wire it up), duplicate `login/` route.
- [ ] Remove the leftover AI-prompt / scratch comments.
- [ ] Fix the `base.html` profile-avatar template bug; stop double-escaping input in `_clean_text_input`.
- [ ] Add DB indexes on hot filter columns; cache filter metadata; replace iframe thumbnails with static thumbnail images.
- [ ] Write a first batch of tests for auth gates, ownership, and upload validation; run them in CI.

---

## A suggested working order (TL;DR)

```
Day 1–2:  Block 1  (secrets, debug, storage, repo hygiene)  ── makes it safe to exist online
Day 3–4:  Block 2  (file access control, headers, error handling)
Week 2:   Block 3  (magic, rate-limit cache, reports table, clickjacking, upload caps)
Ongoing:  Blocks 4 & 5  (defence-in-depth + the architecture consolidation pass)
Then:     re-run `manage.py check --deploy`, re-test the deploy from scratch, launch.
```

---

## Final word

You built a real, multi-feature, OAuth-secured, moderated file-sharing app as a junior dev — and then asked for a brutal review *before* shipping it to real users. That instinct (assume you have blind spots, go looking for them) is the single most important habit in security engineering, and you already have it.

The findings here are concentrated in **config and architecture**, which are the *most fixable* kind — your core application code largely does the right things. Work through Block 1, and you've eliminated everything that could seriously hurt a user. Work through the rest, and you've got something you can genuinely be proud to put your name on.

Go fix Block 1. You've got this. 🚀
