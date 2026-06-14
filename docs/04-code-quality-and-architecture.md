# 🔵 Code Quality, Architecture & "How This Was Built"

None of this is a security hole — but you asked me to be honest about **bad practices, flows, architecture, and the steps used from start to end**. Think of this as a senior dev doing a code walkthrough with you. A lot of it is about habits that will save you (and your future teammates) hours later.

Tone check: the issues below exist *because* you were moving fast and shipping features — which is the right instinct for a student project. The fix is almost never "you're bad at this," it's "here's the convention the ecosystem settled on, and why."

---

## 1. The data model wants normalisation — and you already know it

You left yourself two honest TODO comments:

```python
# models.py:62
#BREAK TABELS !!!! Normalization 1NF 2NF 3NF...

# models.py:70 — reports crammed into one text blob
msg = models.TextField(blank=True, null=True)
```

**What's off:**
- **Reports as concatenated text** (`msg`) is the big one (also [SEC-16](docs/03-medium-and-low-vulnerabilities.md)). Reports are clearly their own entity: who, what reason, when, resolved-or-not. They belong in a `Report` table with a FK to `Record`, not parsed out of `"\n"`-joined strings. As-is you can't count reports, can't mark one resolved, can't sort by most-reported.
- **No `owner` FK** (also [SEC-12](docs/03-medium-and-low-vulnerabilities.md)) — identity is tracked by `uploaded_email` string and `uploaded_by` display string. Add a real FK.
- **Everything is a `CharField`** including `year` (`CharField(max_length=4)`), `semester`, `term`, `session`. These are bounded enumerations — model them as `choices` (or small lookup tables) so the DB enforces validity, not just the form. `year` could be an `IntegerField` with a range validator.
- **`ReportedRecord` is a proxy model** that filters on `msg` being non-empty ([admin.py:87-89](home/admin.py#L87-L89)). That's a clever hack to get a second admin view, but once reports are a real table it becomes a normal `Report` admin instead.

**Why it matters:** the data model is the foundation. Bolting features onto a string column works for 50 papers and collapses at 5,000. Since you're pre-launch, this is the cheapest it will ever be to fix.

**Suggested shape:**
```
Uni 1──* Course 1──* Record 1──* Report
                         │
                         └──* SavedBy (M2M to User)   # you already have this ✅
Record.owner ──> User (FK)                            # add this
```

---

## 2. Business logic living inside `Model.save()`

[models.py:105-150](home/models.py#L105-L150) overrides `save()` to do filesystem I/O (moving files on approval). Beyond the production breakage ([SEC-06](docs/01-critical-vulnerabilities.md)), this is an architectural smell:

- `save()` runs on **every** write — including unrelated `update_fields` saves — so you've had to add guard logic (`if old_instance.status != 'Approved' ...`) to stop it firing wrongly. That complexity is a symptom.
- Side effects (moving files, touching disk) hidden inside `save()` make the model unpredictable and hard to test. A `Record.objects.update(status=...)` would *bypass* it entirely (queryset updates don't call `save()`), so the behaviour isn't even reliable.

**Better:** put the "on approval" action in an explicit place — a service function `approve_record(record)`, an admin action, or a `post_save`/`pre_save` signal *if* you must — and (per SEC-06) ideally don't move files at all. Keep models about *data*, not *workflow*.

---

## 3. Duplicated source-of-truth: the course catalog exists twice

`AVAILABLE_COURSES`, `AVAILABLE_PROGRAMS`, `AVAILABLE_UNIVERSITIES` are defined **identically** in both [views.py:38-152](home/views.py#L38-L152) and [forms.py:5-117](home/forms.py#L5-L117) — and you even left matching comments reminding yourself to update both:

```python
# views.py:37
#Update AVAILABLE_COURSES , AVAILABLE_PROGRAMS, and AVAILABLE_UNIVERSITIES in FORMS.PY also
# forms.py:4
#Update AVAILABLE_COURSES , AVAILABLE_PROGRAMS, and AVAILABLE_UNIVERSITIES in VIEWS.PY also
```

**Why it's a problem:** "update it in two places" is a bug waiting to happen — the day you add a university to one file and forget the other, the form will accept a value the view doesn't recognise (or vice-versa). The comment is a manual workaround for a design smell.

**The fix:** define this data **once** — a single `home/catalog.py` (or, better, the `Uni`/`Course`/program tables in the DB, since you're expanding to more universities anyway) and import it everywhere. Since the whole roadmap of this product is "add more universities," the catalog almost certainly wants to be **data in the database**, editable via admin, not hardcoded Python dicts you redeploy to change.

---

## 4. Leftover AI-prompt comments and scratch notes in production code

These are in committed source:

```python
# views.py:604-606  (inside the report handler)
###########Correct##### no error##### dont UNDO now  safety check point WO HOO no error only security now
            ##3######5232##oA###i 8812 #no
            #n doo not no error safety checko CHECK post p p p p p p p p p p p p p p pp p

# views.py:975
next_url = form.cleaned_data.get('next') or 'home'  ######## Gemini latest prompt DO THE CHANGESS!

# views.py:831
view_mode = ... .lower() #also change
```

**Why it matters:** it's not a security bug, but it's the kind of thing that (a) erodes trust when a reviewer/employer reads your code, (b) hides *real* TODOs in noise, and (c) signals the section was generated/pasted under pressure and may not be fully understood. Clean these out. Use real `# TODO:` notes (or issues) for things that genuinely need follow-up, and delete the rest.

> This is a totally normal thing to leave behind mid-build. The habit to build: a "clean-up pass" (or `git diff` review) before every commit, where leftover scratch comments get deleted.

---

## 5. Dead / unreachable code

- **`login_page` view** ([views.py:967-987](home/views.py#L967-L987)) renders `'home/login.html'` — **which doesn't exist** (you only have `account/login.html`). It would raise `TemplateDoesNotExist` if hit. But it's also **not routed** — `urls.py` maps `login/` to `include('allauth.urls')`, not to `login_page`. So it's dead code referencing a missing template. Delete it (or wire it up and create the template — but allauth already handles login).
- **`compress_image`** ([views.py:692-711](home/views.py#L692-L711)) is defined but never called. The upload flow saves images uncompressed. Either call it in `upload()` (it looks useful for those "huge smartphone photo" uploads you commented about) or remove it. Right now it's a maintenance trap — a reader assumes images are compressed; they aren't.
- **Duplicate auth routes:** `accounts/` *and* `login/` both `include('allauth.urls')` ([urls.py:11,13](UniPapers/urls.py#L11-L13)). Two URL prefixes for the same app is confusing and doubles your surface to reason about. Pick one (`accounts/` is the allauth convention) and redirect the other if you need the short path.

---

## 6. Template correctness bugs (not security, but visibly broken)

In [base.html:592-603](home/templates/home/base.html#L592-L603):

```django
<a href="{% url 'profile' %}" class="profile-icon-btn">
    {% if request.user.profile_pix %}
    <img src="{{ request.user.profile_pix.url }}" alt="Profile">
    {% else %}
    <div class="profile-initial">{{ request.user.username|slice:":1"|upper }}</div>
</a>          {# the </a> only lives in the else branch #}
{% endif %}
```

Two issues:
1. **The `<a>` is only closed in the `{% else %}` branch.** If `profile_pix` were ever truthy, you'd emit an unclosed `<a>` (malformed HTML). It "works" today only because of issue #2.
2. **`request.user.profile_pix` doesn't exist** on Django's default `User` model — Django templates silently swallow missing attributes (they render empty/falsy), so this branch is *always* the `else`. The avatar-image feature is effectively dead. Also, with `ACCOUNT_USERNAME_REQUIRED = False` + email login, `username` is often blank, so the fallback initial can render empty too.

**Fix:** close the `<a>` outside the `if/else`, and either add a real `profile_pix` field to a profile model or drop the image branch. Derive the initial from the email if username is blank.

---

## 7. Input handling: double-escaping corrupts stored data

`_clean_text_input` ([views.py:206-213](home/views.py#L206-L213)) does `strip_tags()` **then** `html.escape()`, and the result is what gets stored in the DB:

```python
value = strip_tags(value)
value = html.escape(value)     # stores  O&#x27;Brien  instead of  O'Brien
```

Then templates render it through Django's auto-escaping **again**. So a title like `Físíca & Química` or `O'Brien` gets stored pre-escaped and then re-escaped on display, showing users literal `O&#x27;Brien` / `&amp;`.

**Why it's the wrong layer:** the correct model is **"store raw, escape on output."** Django already auto-escapes every `{{ variable }}` in templates, so you're protected at render time *for free*. Escaping again on input means:
- data is corrupted at rest (search, exports, and the admin all show mangled text),
- you can never reliably get the original value back,
- it gives a false sense that "input is now safe" — escaping is context-specific (HTML vs JS vs URL), so input-time HTML-escaping isn't even the right defence for non-HTML sinks.

**Fix:** keep `strip_tags()`/length-limiting if you want to forbid markup, but **drop the `html.escape()` on input** and rely on template auto-escaping for output. Store the clean, human-readable value.

---

## 8. Project hygiene & repo setup

These are the "step 0" things that make a project maintainable and deployable:

| Missing thing | Why you need it |
|---|---|
| **`requirements.txt`** (none exists) | Nothing pins your dependencies. Django 6.0.3, allauth 65.x, `dj-database-url`, `cloudinary`, `django-ratelimit`, `python-magic`, `Pillow` — none are declared. Vercel can't build reproducibly, and "works on my machine" becomes "breaks in prod." Run `pip freeze > requirements.txt` (ideally pin from a clean virtualenv). |
| **`.gitignore`** (none exists) | Without it you'll commit `db.sqlite3`, `__pycache__/`, `.env`, media uploads, and secrets. Use GitHub's `Python.gitignore` as a base and add `db.sqlite3`, `/media/`, `/vault/`, `.env`. |
| **`db.sqlite3` in the project root** | A 323 KB SQLite DB is sitting in the repo root even though settings use Postgres. It may contain real user emails/sessions/tokens, and it's directly downloadable via [SEC-03](docs/01-critical-vulnerabilities.md). Delete it, ignore it, and decide on *one* database. |
| **No `vercel.json` / build config** | The settings assume Vercel, but there's no Vercel build/route config or `build_files.sh` to collect static files. The deploy story is incomplete. |
| **Stray media folders** (`papers/`, `uploads/`, `vault/`, `media/`, `home/`-level dupes) | There are real uploaded PDFs/images committed in several top-level folders. Decide on one media location (under a dedicated `MEDIA_ROOT`, ideally object storage) and don't keep user files in source. |
| **`MEDIA_ROOT`/`MEDIA_URL` only defined in the local branch** | On Vercel they're undefined, which is part of why [SEC-06](docs/01-critical-vulnerabilities.md) crashes. Settings should define a coherent storage config for *both* environments. |

---

## 9. No tests — on exactly the code that most needs them

[home/tests.py](home/tests.py) is the empty stub:

```python
from django.test import TestCase
# Create your tests here.
```

You have non-trivial security logic — access gates, ownership checks, upload validation, the approval/move flow, report dedup. Those are precisely the things that break silently during a refactor.

**Start small but high-value.** A handful of tests that would have caught real issues here:
```python
def test_anonymous_cannot_view_pending_paper(): ...
def test_non_owner_cannot_delete_record(): ...
def test_upload_rejects_disallowed_extension(): ...
def test_approved_paper_is_public(): ...
def test_pending_file_is_not_directly_reachable():   # would have flagged SEC-05
```
Even 10 focused tests around auth/uploads is worth more than 100 trivial ones. Wire them into CI so they run on every change.

---

## 10. Performance & scaling (you noted some of this yourself)

For "a lot of people will use this," a few things will bite under load:

- **Heavy pages recompute aggregates every request.** `search` and `profile` run multiple `.count()`, `.distinct()`, and filter queries per request ([views.py:513](home/views.py#L513), [views.py:552-557](home/views.py#L552-L557), [views.py:851-855](home/views.py#L851-L855)). Cache the filter metadata (it changes rarely) and avoid redundant counts.
- **Thumbnail iframes fan out into full file fetches.** Each search/profile card embeds `preview_url` in an `<iframe>` ([search.html:678](home/templates/home/search.html#L678), [profile.html:817](home/templates/home/profile.html#L817)). One page of 10 PDFs = 10 full document downloads through your gated view. Generate cheap static thumbnail images at upload time instead, and show those.
- **No DB indexes on hot filter columns.** You filter constantly on `status`, `uploaded_email`, and `course__{year,program,semester,term,course_name}` and `title`. Add `db_index=True` / `Meta.indexes` for those, and verify with `EXPLAIN` once you have realistic row counts. Without indexes these become table scans as the catalog grows.
- **Everything is synchronous in the request path.** Uploads, the file move, and (future) thumbnail generation all block a worker. As you scale, push slow work (image processing, cleanup) to a background task (Celery/RQ, or a simple cron).

---

## 11. Small stuff worth a glance

- **`view()` orders its checks oddly:** the report POST is handled ([views.py:591](home/views.py#L591)) *before* the slug-correctness `Http404` check ([views.py:628](home/views.py#L628)), while `toggle_save` is handled *after* it. Reports succeed regardless of slug; saves don't. Not a vuln, but inconsistent — pick one order.
- **`ALLOWED_HOSTS` wildcard** `.vercel.app` trusts every Vercel-hosted domain (see [SEC-04](docs/02-high-vulnerabilities.md)). Narrow it.
- **`from .models import *` / `from .forms import *`** ([views.py:24,30](home/views.py#L24)) — wildcard imports make it hard to see what's used and can shadow names. Import explicitly.
- **Django 6.0.3** is a very new release line. Keep an eye on the Django security mailing list and patch promptly; pin the minor version in `requirements.txt` so upgrades are deliberate.

---

### The meta-lesson

If you zoom out, almost every 🔵 item traces back to one root cause: **the project grew feature-by-feature without a "settle the foundations" pass** — one catalog source, one storage strategy, one ownership key, one place for workflow logic, a test net, and pinned deps. That's *completely normal* for a fast-moving solo build. Doing that consolidation pass now, before launch, is the single highest-leverage thing you can do for the next year of this project's life.

Continue to [05-remediation-roadmap.md](docs/05-remediation-roadmap.md).
