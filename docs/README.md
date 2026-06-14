# UniPapers — Security & Code Review

> A pre-launch review of the UniPapers codebase: a Django app where university students upload and share past papers.

Hi — first, the honest headline: **this is a genuinely impressive project for a junior dev.** You built OAuth login with a custom domain allow-list, a moderation/approval workflow, multi-file uploads with MIME sniffing, rate limiting, pagination, a typeahead UI, and you even wrote your own pre-production audit ([PRE_PRODUCTION_AUDIT.md](PRE_PRODUCTION_AUDIT.md)). That shows real engineering instinct and security awareness that a lot of people twice your experience don't have.

This review is deliberately **ruthless about finding problems** — because you asked for that, and because real users deserve it — but none of it is a judgment of you. Every single issue here is a normal, well-trodden mistake. The goal is to teach the *why* behind each one so you walk away a stronger engineer, not just with a patched repo.

There is one thing you must take seriously, though: **in its current state this app should not be deployed to the public internet.** A few of the findings below would let a stranger download your database and secret key. We'll fix those first.

---

## How this review is organised

| File | What's inside |
|------|---------------|
| [01-critical-vulnerabilities.md](docs/01-critical-vulnerabilities.md) | 🔴 Issues that can lead to full compromise. **Fix before any deploy.** |
| [02-high-vulnerabilities.md](docs/02-high-vulnerabilities.md) | 🟠 Serious issues that expose data or break in production. |
| [03-medium-and-low-vulnerabilities.md](docs/03-medium-and-low-vulnerabilities.md) | 🟡 Real weaknesses to fix soon, plus smaller hardening wins. |
| [04-code-quality-and-architecture.md](docs/04-code-quality-and-architecture.md) | 🔵 Non-security: bad practices, data model, dead code, "how this was built". |
| [05-remediation-roadmap.md](docs/05-remediation-roadmap.md) | ✅ A prioritised, do-this-in-order checklist for launch. |

Each finding follows the same shape so it's easy to learn from:

- **What it is** — the problem in plain language
- **Why it's a risk** — the security/engineering principle behind it
- **How it's exploited** — a concrete attacker walkthrough (so it's not abstract)
- **The fix** — what to change, usually with code
- **What you did right** — because a lot of these are *almost* correct

---

## Severity legend

| Badge | Meaning |
|-------|---------|
| 🔴 Critical | Direct path to secret/data/account compromise. Stop-ship. |
| 🟠 High | Sensitive data exposure or a production-breaking flaw. |
| 🟡 Medium | Exploitable under conditions, or meaningful abuse/DoS surface. |
| 🟢 Low | Defense-in-depth, hardening, or low-likelihood issues. |
| 🔵 Quality | Not a vulnerability — maintainability, architecture, correctness. |
| ✅ Strength | Something you got right. Keep doing it. |

---

## Findings at a glance (by type)

This is the "categorised by type" view you asked for. IDs (e.g. `SEC-01`) are stable references used across all the docs.

### Secrets & Configuration Management
| ID | Severity | Finding |
|----|----------|---------|
| SEC-01 | 🔴 Critical | Hardcoded `django-insecure` `SECRET_KEY`, also used in production |
| SEC-02 | 🔴 Critical | Database password hardcoded in `settings.py` |
| SEC-04 | 🟠 High | No production security headers (HSTS, secure cookies, SSL redirect, nosniff) |
| SEC-13 | 🟢 Low | OAuth access tokens stored in the database unnecessarily |

### Insecure Deployment / Debug Exposure
| ID | Severity | Finding |
|----|----------|---------|
| SEC-03 | 🔴 Critical | `DEBUG=True` off-Vercel **+** `MEDIA_ROOT = BASE_DIR` serves the entire project at `/media/` |

### Broken Access Control (OWASP A01)
| ID | Severity | Finding |
|----|----------|---------|
| SEC-05 | 🟠 High | Raw file URLs bypass the "approved-only" gate (IDOR / enumeration of pending uploads) |
| SEC-12 | 🟢 Low | Ownership keyed on a mutable email string instead of a user foreign key |

### File Upload & Storage Handling
| ID | Severity | Finding |
|----|----------|---------|
| SEC-06 | 🔴 Critical | Approval moves files on the local filesystem — breaks on Vercel/Cloudinary |
| SEC-07 | 🟠 High | Approval overwrites files by basename (`os.replace`) — silent data loss |
| SEC-10 | 🟡 Medium | MIME validation "fails open" when `python-magic` is missing |
| SEC-11 | 🟡 Medium | No aggregate upload cap; orphaned files on partial failure |

### Information Disclosure
| ID | Severity | Finding |
|----|----------|---------|
| SEC-08 | 🟠 High | Raw exception text returned to users on upload failure |

### Clickjacking / Browser Security Policy
| ID | Severity | Finding |
|----|----------|---------|
| SEC-09 | 🟡 Medium | `paper_preview` is `@xframe_options_exempt` (framable by any site) |
| SEC-15 | 🟢 Low | No Content-Security-Policy; inline scripts and un-pinned CDN assets |

### Authentication & Session
| ID | Severity | Finding |
|----|----------|---------|
| SEC-14 | 🟢 Low | `ACCOUNT_LOGOUT_ON_GET` / `SOCIALACCOUNT_LOGIN_ON_GET` enable GET-based CSRF |

### Abuse / Denial of Service
| ID | Severity | Finding |
|----|----------|---------|
| SEC-16 | 🟡 Medium | Report feature is spammable and grows an unbounded text column |
| SEC-17 | 🟡 Medium | Rate limiting uses per-process memory — ineffective on serverless/multi-worker |

See [04-code-quality-and-architecture.md](docs/04-code-quality-and-architecture.md) for the 🔵 non-security findings (data model, dead code, duplication, tests, the leftover AI-prompt comments, etc.).

---

## The good news (things you genuinely did right) ✅

So this doesn't read as all-negative — these are real wins:

- **No SQL injection anywhere.** You used the Django ORM (`.filter()`, `Q()`, `icontains`) throughout and never touched raw SQL or string-built queries. This is the single most common catastrophic web bug and you avoided it entirely.
- **CSRF protection is intact.** Middleware is on and every state-changing form has `{% csrf_token %}`.
- **Filename sanitisation is correct.** You consistently reduce uploaded names to their basename and rebuild safe storage names, which blocks path-traversal-via-filename — a classic upload bug.
- **Form `ChoiceField` validation** locks dropdown inputs to server-side allow-lists. That's the right instinct.
- **You used `json_script`** to pass data to JavaScript (in [upload.html](home/templates/home/upload.html#L472)) — the *correct*, XSS-safe way, not raw interpolation.
- **PKCE is enabled** on the Google OAuth flow.
- **You served previews with an extension-derived content type**, which neutralises most "upload an HTML file" stored-XSS attempts.
- **You wrote your own audit first.** That mindset is the thing that actually makes a good engineer.

Now let's make it bullet-proof. Start with [01-critical-vulnerabilities.md](docs/01-critical-vulnerabilities.md).
