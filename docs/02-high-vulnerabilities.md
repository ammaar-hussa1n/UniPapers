# 🟠 High Vulnerabilities

These won't always hand over the keys to the kingdom by themselves, but they expose user data, leak internals, or break in production. Fix them in the same release as the criticals.

---

## SEC-05 — Raw file URLs bypass the "approved-only" access check (IDOR / enumeration)

**Type:** Broken Access Control (OWASP A01) · **Severity:** 🟠 High
**Location:** the gate at [views.py:577-579](home/views.py#L577-L579) & [views.py:670-671](home/views.py#L670-L671) vs. how files are actually served ([view_paper.html:834](home/templates/home/view_paper.html#L834), [views.py:332-366](home/views.py#L332-L366))

### What it is
Your permission logic is solid **at the Django view layer**: `view` and `paper_preview` both check `if record.status != 'Approved' and not (is_admin or is_owner): block`. So a stranger can't open the *HTML page* of a pending paper. 👍

But the actual **file bytes** aren't served through those views. They're served directly:
- Templates link/embed `{{ record.file.url }}` and `{{ attachment.url }}` (e.g. the `<img>` preview and the JS `fileUrl`).
- In local/dev that URL is `/media/...` served by the static handler (no auth).
- In production that's a **public Cloudinary URL** (no auth).

The status check guards the *page*, not the *object*. The object has its own unauthenticated URL.

### Why it's a risk
"Pending" papers are, by definition, **un-moderated** — they might be copyrighted, wrong, spam, or (per your own report categories) "Inappropriate content." Until an admin approves them, they should not be publicly reachable. Right now they are, if someone knows or guesses the URL.

And the URLs are **guessable**. Single-file uploads are stored under their *original basename* ([views.py:797](home/views.py#L797)), so a pending file is at `/media/vault/pending/Lab6.pdf`, `/media/vault/pending/midterm.pdf`, etc. An attacker can enumerate common names.

### How it's exploited
1. Attacker (not logged in) requests `https://yoursite/media/vault/pending/Lab6.pdf` — or scripts a wordlist of likely paper names.
2. The static handler / Cloudinary serves the file with **no status check and no auth**.
3. They now have content that was never approved for public release — bypassing your entire moderation workflow.

This is a textbook **IDOR (Insecure Direct Object Reference)**: the access decision lives in the view, but the resource is reachable by direct reference without that decision.

### The fix
Serve **all** file bytes through a permission-checked Django view, and keep the raw storage URLs private.

- You already have `paper_preview` — make it the *only* way to fetch a file, and apply the same status/owner/admin gate there (it already does ✅). Then:
  - In templates, never emit `record.file.url` for non-public files. Use the gated `preview_url` everywhere (downloads, `<img>`, the JS `fileUrl`).
  - For pending files specifically, don't expose any direct URL.
- For private storage on Cloudinary/S3, use **signed, expiring URLs** generated only after the permission check passes (Cloudinary "authenticated"/"private" delivery, or S3 pre-signed URLs), instead of public URLs.
- Defence in depth: store pending uploads under an **unguessable key** (you already do this for multi-image batches with a UUID — do it for *all* uploads, including single PDFs) so enumeration can't work even if a URL leaks.

> **Teaching note:** the principle is *"authorise the object, not just the page."* Any time a resource has its own URL, that URL needs to enforce the same rules as the page that links to it.

---

## SEC-04 — Production security headers are all missing (no HSTS, secure cookies, SSL redirect, nosniff)

**Type:** Security Misconfiguration · **Severity:** 🟠 High
**Location:** absent from [UniPapers/settings.py](UniPapers/settings.py) entirely

### What it is
None of Django's standard production-hardening settings are present:

| Setting | Current | Should be (prod) |
|---|---|---|
| `SESSION_COOKIE_SECURE` | default `False` | `True` |
| `CSRF_COOKIE_SECURE` | default `False` | `True` |
| `SECURE_SSL_REDIRECT` | default `False` | `True` |
| `SECURE_HSTS_SECONDS` | unset | `31536000` (+ include-subdomains, preload) |
| `SECURE_CONTENT_TYPE_NOSNIFF` | default `False` | `True` |
| `SECURE_PROXY_SSL_HEADER` | unset | `('HTTP_X_FORWARDED_PROTO','https')` behind a proxy |
| `SESSION_COOKIE_HTTPONLY` | default `True` ✅ | keep |

Running `python manage.py check --deploy` will flag all of these.

### Why it's a risk
- Without `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE`, the session and CSRF cookies can be sent over plain HTTP, where a network attacker (open Wi-Fi, malicious proxy) can read them → **session theft**.
- Without `SECURE_SSL_REDIRECT` + HSTS, a user can be downgraded to HTTP and stripped of TLS (sslstrip-style).
- Without `SECURE_CONTENT_TYPE_NOSNIFF`, a browser may MIME-sniff a response and treat an uploaded file as something executable.

### How it's exploited
1. Victim opens the site on coffee-shop Wi-Fi, first request goes over HTTP (no redirect, no HSTS).
2. Attacker on the same network reads the session cookie (it isn't marked `Secure`, so it's sent in clear).
3. Attacker replays the cookie and is now the victim.

### The fix
Add a production block, gated on not-DEBUG:
```python
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')  # Vercel terminates TLS at the edge
```
Also tighten `ALLOWED_HOSTS`: today it's `['localhost', '127.0.0.1', '.vercel.app']`. The `.vercel.app` wildcard means *every* Vercel app domain is trusted — fine-ish for previews, but for production set it to your real domain(s) and only your project's `*.vercel.app` host. Make it env-driven.

Then verify with:
```bash
python manage.py check --deploy
```

---

## SEC-07 — Approval overwrites files by basename (`os.replace`) → silent data loss

**Type:** File Storage / Data Integrity · **Severity:** 🟠 High
**Location:** [home/models.py:117-129](home/models.py#L117-L129)

```python
filename = os.path.basename(old_path)
new_path = f'uploads/papers/{filename}'
...
os.replace(old_full_path, new_full_path)   # overwrites silently if new_full_path exists
```

### What it is
When a paper is approved, it's moved to `uploads/papers/<original-basename>`. `os.replace` **overwrites** any existing file at the destination without warning. Django's storage layer auto-dedupes names *on upload* (that's why you see `..._wPdiytd.pdf` copies), but this raw `os.replace` in `save()` **does not** dedupe — it clobbers.

### Why it's a risk
Past-paper filenames are extremely generic and collision-prone: `Lab6.pdf`, `midterm.pdf`, `finalterm.pdf`, `Maths_4024_P2_2013.pdf`. Two different students approving two different "Lab6.pdf" files means the second overwrites the first.

### How it's exploited
- **Accidental:** routine moderation of common filenames silently destroys earlier papers. One record's DB row now points at a *different* student's file — an integrity and privacy problem.
- **Deliberate:** a user uploads a file deliberately named to match an existing approved paper, waits for approval, and overwrites/defaces the target paper's content.

### The fix
- Best: adopt [SEC-06](docs/01-critical-vulnerabilities.md) Option A — **don't move files**, so there's no destination collision to worry about.
- If you keep separate folders, generate a collision-resistant destination key (UUID prefix) and use the storage API's `get_available_name` / `save` (which dedupes) instead of `os.replace`. Never overwrite blindly.
- As a rule: storage keys for user content should be **unique and opaque** (e.g. `uploads/papers/<uuid>.<ext>`), with the human-friendly name kept in a DB column for display/download.

---

## SEC-08 — Raw exception text leaked to users on upload failure

**Type:** Information Disclosure · **Severity:** 🟠 High
**Location:** [home/views.py:810-812](home/views.py#L810-L812)

```python
except Exception as exc:
    messages.error(request, f"Upload handling failed: {str(exc)}")
    return redirect('upload')
```

### What it is
The upload handler catches *everything* and shows the raw exception string to the user via a flash message.

### Why it's a risk
`str(exc)` can contain absolute filesystem paths, storage backend errors, database error fragments, Cloudinary API messages, and other internals. That's a free reconnaissance gift to an attacker probing the upload path.

### How it's exploited
1. Attacker crafts inputs that trigger different failure modes (oversized file, weird encoding, storage hiccup).
2. The error message reveals, say, `[Errno 13] Permission denied: '/var/task/vault/pending/...'` or a Cloudinary key error — disclosing paths, storage layout, and config.
3. They use that map to target follow-on attacks.

### The fix
Log the detail server-side; show the user a generic message:
```python
import logging
logger = logging.getLogger(__name__)
...
except Exception:
    logger.exception("Upload handling failed for user %s", request.user.pk)
    messages.error(request, "Something went wrong while saving your upload. Please try again.")
    return redirect('upload')
```
Also avoid the blanket `except Exception` where you can — catch the specific errors you expect (validation, storage) and let truly unexpected ones surface to your error tracker.

> Same pattern, lower stakes, applies anywhere you interpolate internal state into a user-facing `messages.error(...)`.

---

## SEC-?? note — these compound with the criticals

[SEC-05](docs/02-high-vulnerabilities.md#sec-05--raw-file-urls-bypass-the-approved-only-access-check-idor--enumeration) (direct file access) plus [SEC-03](docs/01-critical-vulnerabilities.md) (project served at `/media/`) means that, on a non-Vercel deploy, *not only* pending papers but the **entire source tree and database** are reachable through the same unauthenticated `/media/` surface. The fixes reinforce each other: gate file serving (SEC-05), and never root your media at the project (SEC-03).

Continue to [03-medium-and-low-vulnerabilities.md](docs/03-medium-and-low-vulnerabilities.md).
