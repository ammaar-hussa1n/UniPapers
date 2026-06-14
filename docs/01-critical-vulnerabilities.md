# 🔴 Critical Vulnerabilities

These are stop-ship. Each one, on its own, can hand an attacker your secret key, your database, or your users' sessions. Fix every item here **before** the app touches the public internet.

---

## SEC-01 — Hardcoded `SECRET_KEY` (and it's the throwaway dev key)

**Type:** Secrets Management · **Severity:** 🔴 Critical
**Location:** [UniPapers/settings.py:25](UniPapers/settings.py#L25)

```python
SECRET_KEY = 'django-insecure-g_xys5e&3ku0a3bwm=h$yc4sb5$a(3(pm7+k@1^qgi7z8cmk9-'
```

### What it is
The Django `SECRET_KEY` is the master key Django uses to cryptographically sign things: session cookies, password-reset tokens, `signing.dumps()` payloads, CSRF tokens (partly), messages, etc. Here it's (a) written directly in source, and (b) the auto-generated `django-insecure-` development key — Django literally prefixes it with the word *insecure* to warn you. Critically, **there is no environment-variable override**, so the Vercel production deployment uses this exact key too.

### Why it's a risk
Anyone who can read this one line can forge any signed value your app trusts. The whole point of a secret key is that it's *secret*; a key in source control (or in a deployment bundle, or pasted in a screenshot) is not.

### How it's exploited
1. Attacker obtains the key — from a leaked repo, a misconfigured `/media/` route (see [SEC-03](docs/01-critical-vulnerabilities.md#sec-03--debugtrue-off-vercel--media_root--base_dir--whole-project-served-at-media), which literally lets them download `settings.py`), a backup, or a screenshot.
2. They craft a forged session cookie that points at an admin user, signing it with the known key.
3. Django's session framework validates the signature (it checks out — same key), and the attacker is now logged in as an admin without ever knowing a password.

This is full authentication bypass. The same key also lets them forge password-reset and any other `signed` data.

### The fix
1. Read it from the environment, with a hard failure if missing in production:
   ```python
   import os
   SECRET_KEY = os.environ['DJANGO_SECRET_KEY']   # crash loudly if unset — don't default
   ```
2. Generate a fresh random key (never the `django-insecure` one):
   ```python
   python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
   ```
3. Set it as `DJANGO_SECRET_KEY` in Vercel's environment variables (and your local `.env`).
4. **Rotate it** — assume the current value is already burned. Rotating invalidates all existing sessions, which is exactly what you want.

> **Teaching note:** the rule is *"secrets come from the environment, code comes from the repo."* The repo is shared, copied, and backed up; the environment is per-deployment and access-controlled. Keep them separate forever.

---

## SEC-02 — Database password hardcoded in settings

**Type:** Secrets Management · **Severity:** 🔴 Critical
**Location:** [UniPapers/settings.py:154-162](UniPapers/settings.py#L154-L162)

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'unipapers_db',
        'USER': 'postgres',
        'PASSWORD': 'MoskI@507',
        'HOST': '127.0.0.1',
        ...
```

### What it is
Your local Postgres superuser password (`postgres` / `MoskI@507`) is sitting in plaintext in source. The `postgres` role is the database *superuser* — it can read/write/drop every database on that server.

### Why it's a risk
Same principle as SEC-01: a credential in source is not a secret. And people reuse passwords — if `MoskI@507` is used anywhere else (other DBs, your own accounts), the blast radius is larger than this one app.

### How it's exploited
- If this file is ever exposed (see [SEC-03](docs/01-critical-vulnerabilities.md#sec-03--debugtrue-off-vercel--media_root--base_dir--whole-project-served-at-media)), an attacker on your network — or anyone who can reach port 5432 — logs in as the Postgres superuser and owns your data.
- Even "local only" leaks: this becomes part of any zip/backup/screenshot you share.

### The fix
- Move local DB config to environment variables too (you already use `dj_database_url` for prod — use it for local as well):
  ```python
  DATABASES = {'default': dj_database_url.config(default=os.environ['DATABASE_URL'])}
  ```
  Then set `DATABASE_URL=postgres://user:pass@127.0.0.1:5432/unipapers_db` in a local `.env` (and load it with `python-dotenv` or `django-environ`).
- **Rotate the Postgres password**, and create a dedicated least-privilege app role instead of using `postgres` (the app needs `CONNECT`, `SELECT/INSERT/UPDATE/DELETE` on its tables — not superuser).
- Add a `.gitignore` (you don't have one — see [04](docs/04-code-quality-and-architecture.md)) and make sure `.env`, `db.sqlite3`, and media folders are never committed.

---

## SEC-03 — `DEBUG=True` off-Vercel **+** `MEDIA_ROOT = BASE_DIR` → the whole project is served at `/media/`

**Type:** Insecure Deployment / Information Disclosure · **Severity:** 🔴 Critical (this is the scariest finding in the review)
**Location:** [settings.py:29-30](UniPapers/settings.py#L29-L30), [settings.py:165-166](UniPapers/settings.py#L165-L166), [urls.py:28-29](UniPapers/urls.py#L28-L29)

```python
# settings.py
IS_VERCEL = os.environ.get('VERCEL') == '1'
DEBUG = not IS_VERCEL          # True everywhere that isn't Vercel
...
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR          # <-- the PROJECT ROOT, not a media subfolder

# urls.py
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
```

### What it is
Two mistakes that combine into a disaster:

1. **`DEBUG` is "on unless proven off."** It's `True` for *any* environment that isn't Vercel — a VPS, Render, a Docker box, a teammate's laptop, a staging server. Debug mode shows full stack traces with source, settings, and local variables on every error page.
2. **`MEDIA_ROOT = BASE_DIR`.** Media root is supposed to be a dedicated *uploads* folder. You pointed it at the entire project directory. Then `urls.py` wires up `static('/media/', document_root=BASE_DIR)` whenever `DEBUG` is on.

Together: **`/media/<anything>` serves any file in your project tree.**

### Why it's a risk
The `/media/` route now exposes your whole codebase and data, with no authentication:

- `GET /media/UniPapers/settings.py` → returns the source of `settings.py` → leaks **SEC-01** (`SECRET_KEY`) and **SEC-02** (DB password) directly.
- `GET /media/db.sqlite3` → downloads your SQLite database (it's sitting in the project root, 323 KB) → all user emails, sessions, OAuth tokens.
- `GET /media/manage.py`, `/media/home/views.py`, etc. → full source disclosure.

### How it's exploited
1. App is deployed anywhere other than Vercel (very likely at some point — staging, a cheap VPS, a demo box). `DEBUG` is `True`.
2. Attacker requests `https://yoursite/media/UniPapers/settings.py`.
3. Django's `static()` serve view reads from `document_root` (= project root) and streams the file back. (It blocks `../` traversal, but it doesn't need to — the root *is* the whole project.)
4. Attacker now has the secret key (→ forge admin session, SEC-01) and the DB password (SEC-02). Game over.

This single finding chains into the other two criticals. It's the reason "just don't deploy yet" is the right call.

### The fix
- **Gate `DEBUG` on a positive opt-in, default off:**
  ```python
  DEBUG = os.environ.get('DJANGO_DEBUG') == '1'   # off unless you explicitly turn it on
  ```
- **Point `MEDIA_ROOT` at a dedicated folder**, never the project root:
  ```python
  MEDIA_ROOT = BASE_DIR / 'media'
  ```
  (And move your existing `uploads/`, `vault/`, `papers/` under it, or reconfigure paths.)
- **Don't serve media via Django in production at all.** The `static()` helper is dev-only by design. In production, serve uploads from object storage (Cloudinary, S3) or a properly configured web server / CDN — never from a route whose document root is anywhere near your source.
- Lock `ALLOWED_HOSTS` to your real domains (currently includes the wildcard `.vercel.app` — see [02-high](docs/02-high-vulnerabilities.md)).

> **Teaching note:** `DEBUG=True` in production is OWASP's "Security Misconfiguration" poster child. The mental model: *production is hostile by default; you opt into debugging, you never opt out of it.*

---

## SEC-06 — File-approval workflow moves files on the local filesystem (breaks on the actual production target) (DID THIS !#########)

**Type:** Architecture / File Storage · **Severity:** 🔴 Critical (data-integrity + production-breaking)
**Location:** [home/models.py:105-150](home/models.py#L105-L150) (the `Record.save()` override) and [home/models.py:13-42](home/models.py#L13-L42) (`_move_uploaded_image_batch`)

```python
def save(self, *args, **kwargs):
    ...
    old_full_path = os.path.join(settings.MEDIA_ROOT, old_path)
    new_full_path = os.path.join(settings.MEDIA_ROOT, new_path)
    os.makedirs(os.path.dirname(new_full_path), exist_ok=True)
    if os.path.exists(old_full_path):
        os.replace(old_full_path, new_full_path)   # raw OS file move
```

### What it is
When an admin flips a paper's status to `Approved`, your model's `save()` physically moves the file from `vault/pending/` to `uploads/papers/` using `os.path`, `os.makedirs`, and `os.replace` against `settings.MEDIA_ROOT`. That's a **local-disk operation**. But your production config ([settings.py:135-151](UniPapers/settings.py#L135-L151)) switches storage to **Cloudinary** on Vercel — and **`MEDIA_ROOT` isn't even defined on the Vercel branch** (it's only set in the `else:` local branch).

### Why it's a risk
On Vercel:
- `settings.MEDIA_ROOT` raises `AttributeError` the moment an approval runs (it's referenced but never defined in the `IS_VERCEL` branch).
- Even if it were defined, Vercel's filesystem is **ephemeral and read-only** outside `/tmp`, and the file lives in **Cloudinary**, not on local disk. There's nothing at `old_full_path` to move.

So your core moderation action — approving a paper — **crashes or silently no-ops in production.** The app looks fine in local testing (where the disk is real and writable) and then fails on launch day at the first approval.

### How it's exploited
This one doesn't need an attacker — *normal use breaks it*:
1. A student uploads a paper (lands in Cloudinary, name still starts with `vault/pending/`).
2. Admin clicks "Approved" in `/admin/`.
3. `Record.save()` tries to `os.replace` a Cloudinary file using an undefined `MEDIA_ROOT` → 500 error / `AttributeError`, or the file is "approved" in the DB but never actually relocated, leaving previews and downloads broken.

The data integrity angle: the DB row may say `uploads/papers/x.pdf` while the file still physically lives in `vault/pending/x.pdf` (or vice-versa), so `record.file.url` points at nothing.

### The fix
Stop moving bytes around. Two clean options:

- **Option A (recommended): don't move files at all.** Keep one immutable storage path and represent "pending vs approved" purely as the `status` column. Filter on `status` in queries (you already do). No file ever moves; approval is a one-field DB update. This is storage-agnostic and works identically on local disk, S3, and Cloudinary. (DID THIS !!!!!!!!!!!!!!!!!!!)
- **Option B: if you must separate folders, use the storage API, not the OS:**
  ```python
  from django.core.files.storage import default_storage
  with default_storage.open(old_path) as f:
      default_storage.save(new_path, f)
  default_storage.delete(old_path)
  ```
  This goes through whatever backend is configured (Cloudinary included) instead of assuming local disk. But Option A is simpler and less error-prone.

Either way, move this logic **out of `Model.save()`** (see [SEC-07](docs/02-high-vulnerabilities.md) and the architecture notes) — overriding `save()` to do filesystem I/O is fragile and runs on every save.

> **Teaching note:** the deeper lesson is *pick one storage architecture.* Right now the code is half-local-disk and half-cloud, and the two halves contradict each other. Decide "all Cloudinary" (or "all S3", or "all local") and make every code path honour that one choice.

---

## Why these four are grouped as Critical

Notice how they **chain**:

```
SEC-03 (/media/ serves the project)
   └── leaks SEC-01 (SECRET_KEY)  ──> forge admin session ──> full account takeover
   └── leaks SEC-02 (DB password) ──> direct database compromise
SEC-06 ── breaks the core workflow the moment you go live on the intended host
```

A single misconfigured deploy turns into total compromise. That's why the roadmap ([05](docs/05-remediation-roadmap.md)) puts "secrets + debug + storage" as the very first block of work, before anything else.

Continue to [02-high-vulnerabilities.md](docs/02-high-vulnerabilities.md).
