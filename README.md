# hum.log — backend

**human.log.** A blog you have to type.

The rule is one line long: you cannot paste text into hum.log. Not a
paragraph, not a sentence, not a word. If it is on the page, a person put it
there a keystroke at a time. Images are the exception, because nobody types a
photograph.

That rule is enforced in the browser, where the clipboard is — the frontend
(`humlog_ui`) blocks paste on desktop and on mobile. This repository is the API
behind it, and it deliberately does not try to police what it is sent: a server
cannot tell typing from pasting, and pretending otherwise would only produce a
check that is annoying to honest writers and trivial to route around. What the
backend does instead is make *not* pasting bearable — because the reason people
paste is usually that they want to move something, and moving things is what
the block model below is for.

FastAPI, Postgres, S3 for images, one container.

---

## The idea: a post is a list of rows

A post is not a document with a body column. It is a list of **blocks**, one
row each, and a block is one line of markdown.

```
blogs        one post
  blocks     one paragraph, image, table, code fence or rule — each a row
    blocks   nested, up to four levels deep
  images     the files, in S3
  comments   from anyone, no account, on published posts only
```

Three consequences, and they are the whole design:

**A newline is a block boundary.** Press Enter and you are in a new row. This
is enforced in `app/services/block_service.py`, not in the client: content
arriving with newlines in it is split into one block per line before it is
stored, so the invariant holds whatever is talking to the API. Code fences and
tables are exempt — a newline inside those is part of the thing.

**Moving a block is one UPDATE.** Blocks are ordered by a *fractional index*:
`order_key` is a short base-62 string, and between any two strings there is
always another one. Dropping a block between two others rewrites that block's
key and touches nothing else. A thousand-paragraph post reorders as fast as a
three-paragraph one, and two people dragging at the same time cannot renumber
each other. The generator is the `fractional-indexing` package, wrapped in
`app/blocks/ordering.py`.

> One trap worth knowing about: these keys mean what they mean in **ASCII**
> order, where `"Zz" < "a0"`. A Postgres database created with an
> `en_US.UTF-8` collation does not sort that way. So nothing in this codebase
> ever writes `ORDER BY order_key` — a post's blocks are loaded in one query
> and sorted in Python. If you ever want the sort in SQL, the column needs
> `COLLATE "C"`, not just an index.

**The storage format is the export format.** Blocks hold markdown as typed,
so `GET .../export?format=markdown` on a post with no nesting is byte-for-byte
what its author wrote. HTML export renders that markdown and sanitises the
result, because an exported post is a file a reader opens and its author is
not necessarily somebody they know.

### The editing rules

Deliberately few, because they have to be obvious while somebody is typing.

| Gesture | What happens |
| --- | --- |
| Enter | A new block, immediately after the one you were on, as its sibling |
| A newline in the text | Splits into one block per line, server-side |
| Tab | Nests the block under the block above it **at its own level**. Again to go deeper |
| Shift+Tab | Pops it out to sit just after its old parent |
| ↑ / ↓ | Swaps with the sibling above or below. Never changes parent or depth |
| Drag | Lands it under any parent, after any sibling |
| Delete | Keeps the children and promotes them into the gap. `?cascade=true` takes the subtree |

The arrows are forgiving on purpose: a block already at the top of its list
comes back unchanged rather than as an error, so the button can be held down.
Nesting stops at four levels (`MAX_BLOCK_DEPTH`), and a block cannot be dropped
inside its own subtree.

---

## The API

Everything lives under `/api`. Authenticated routes take a bearer token;
`/api/public/*` takes nothing at all.

### Accounts — `/api/auth`

| | |
| --- | --- |
| `POST /register` | username, email, password. Both username and email are unique |
| `POST /login` | `identifier` is a username *or* an email — whichever they remember |
| `POST /refresh` · `POST /logout` | Refresh tokens rotate; the presented one is spent |
| `GET /me` · `PATCH /me` | Display name and bio |
| `POST /password` | Change it. Logs every session out |
| `POST /forgot-password` | Emails an eight-letter code |
| `POST /reset-password` | Spend the code, set a new password |

Passwords are PBKDF2-SHA256, 100,000 iterations, per-password salt, stored as
`iterations$salt$hash` so the cost can be raised later without invalidating
anybody's existing hash.

Reset codes are eight letters from a 24-letter alphabet with no `I`, `O`, `0`
or `1` in it, because somebody is going to copy this off their phone by hand.
Only the SHA-256 is stored, they last fifteen minutes, five wrong guesses burns
one, and asking for a new one invalidates the old. `POST /forgot-password`
answers identically whether or not the address exists — and sends on a
background thread, so it does not answer *faster* for an address that doesn't.

### Posts — `/api/blogs`

`POST` to start one (it arrives empty; that is the normal first state),
`PATCH` for title, summary, slug, cover and whether comments are on,
`POST .../publish` and `.../unpublish`, `DELETE` to remove the post, its
blocks, its comments and its files.

Renaming a *published* post leaves its slug alone unless you pass one
explicitly. The old URL is out in the world and quietly breaking it is worse
than a slug that no longer matches the title. `published_at` is stamped once
and never again, so unpublishing to fix a typo doesn't jump the post back to
the top of the feed.

### Blocks — `/api/blogs/{blog_uuid}/blocks`

`GET` the lot in reading order, each carrying its `depth`. `POST` to add,
`PATCH` to edit, `DELETE` to remove, and `.../move`, `.../indent`,
`.../outdent` for the gestures in the table above.

Writes return a **list** of blocks, not one, because of the newline rule —
typing one line gives one block back, typing several gives several.

The block uuid is looked up *within* the post in the path, so knowing a uuid is
never enough to reach somebody else's paragraph.

### Images — the one thing you can paste

`POST /api/blogs/{uuid}/images` (multipart) stores the file and returns a row;
turning that into something a reader sees is a separate `POST .../blocks` with
the `image_uuid`. Two calls, so a failed upload never leaves an empty block
behind.

Two ways to read one back. `/api/images/{uuid}/raw` streams the bytes through
this server at a URL that keeps working — that is what exports and public pages
use, because a presigned link in a downloaded file is dead within the hour.
`/api/images/{uuid}/url` hands back a short-lived signed link straight to
storage, which is cheaper and what the editor should use while somebody is
working. Draft images are as private as the draft.

10 MB, and `jpeg`/`png`/`gif`/`webp`/`avif`. No SVG: an SVG is a document that
can run script, and these files are served back from this origin to readers who
did not write them.

### Reading — `/api/public`

| | |
| --- | --- |
| `GET /blogs` | The feed, newest first. `author`, `q`, `limit`, `offset` |
| `GET /blogs/{uuid}` · `GET /posts/{username}/{slug}` | A post and its blocks |
| `GET /blogs/{uuid}/export` | Markdown or HTML, same code path as the author's |
| `GET /authors/{username}` | One writer |
| `GET` · `POST /blogs/{uuid}/comments` | Read and leave comments |

A draft is 404 from every one of these, not 403 — whether an unpublished post
exists is part of what being unpublished means.

### Comments

Anyone can leave one. No account, no email required, a name is optional.
Keeping that survivable without asking readers who they are:

- Comments exist only on published posts with comments switched on.
- Every commenter is remembered as a **salted hash** of their address — enough
  to rate-limit them and enough for the author to sweep away everything they
  wrote (`DELETE .../comments/{uuid}?same_commenter=true`), and not enough to
  be a record of who read what.
- The same address posting the same words twice inside ten minutes is a double
  submit, not a second opinion, and is refused.
- 6 comments per address per ten minutes in memory; 20 per address per post per
  hour in the database, so a restart doesn't reset it.
- A honeypot field (`website`) that no human ever sees. Anything in it is
  answered as a success, so whatever filled it in gets no signal to adapt to.

Replies nest exactly one level. A reply to a reply attaches to the top of its
thread, which is the whole of what a comment section under a blog post needs
and removes the question of how deep the frontend has to render.

### Export

`GET /api/blogs/{uuid}/export?format=markdown|html`. JSON by default so a
client can copy the text; `&download=true` returns the same bytes as an
attachment; `&include_header=false` leaves off the title and byline. Add
`&bundle=true` for a portable zip containing the document and an `images/`
folder, with every image reference rewritten to a relative path.
Published posts export from `/api/public/...` too, through the same code, so
what a reader downloads is what the writer would have got.

Nesting has no direct equivalent in markdown — there is no way to put a
paragraph under a paragraph — so a child block becomes a list item, indented
two spaces per level, unless it already starts with a marker of its own. A run
of them comes out as one tight list. Top-level blocks are written out
untouched.

---

## Rate limits

`app/api/rate_limit.py`, a sliding window per IP, held **in this process**.
That is why `k8s/base/deployment.yaml` says `replicas: 1` — a second replica
would quietly double every limit here. Scaling out means moving the counters to
Redis first. Limits that must survive a restart are counted in Postgres
instead, in `app/services/comment_service.py`.

| Bucket | Limit |
| --- | --- |
| register, refresh | 20 / minute |
| login, reset-password | 8 / minute |
| forgot-password | 5 / hour |
| comments | 6 / 10 minutes |
| image uploads | 60 / 10 minutes |
| anonymous reads | 300 / minute |

The container runs uvicorn with `--forwarded-allow-ips *`, without which every
anonymous commenter arrives wearing the ingress's address and they all share
one limit.

---

## Running it locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in SECRET_KEY at minimum

export SECRET_KEY="$(openssl rand -hex 32)"
export DATABASE_URL="sqlite:///humlog_dev.db"   # or a real Postgres URL

python scripts/dev_seed.py    # a writer, a published post, a draft, comments
python main.py                # http://localhost:8000/docs
```

SQLite is fine for local work — the app only needs Postgres for the deployment.
Object storage and SMTP are both optional: without them, image uploads answer
502 and password resets are logged and skipped, and nothing else changes.

```bash
pip install pytest && pytest        # 81 tests, no database or network needed
```

The tests are worth reading before changing the ordering or nesting rules:
`tests/test_blocks.py` is every editing gesture written down as an assertion
about what the post looks like afterwards.

### Migrations

The app runs `alembic upgrade head` itself on startup, so a deploy that adds a
migration applies it with no separate step. To add one:

```bash
alembic revision --autogenerate -m "what it does"
alembic upgrade head
```

`USE_ALEMBIC_MIGRATIONS=false` falls back to `create_all()`, which builds the
current schema but records no revision.

---

## Deployment

Same shape as every other backend in this cluster: build on push to `main`,
write the new tag into the overlay, let Argo CD notice.

```
.gitea/workflows/build-and-push.yml    → Gitea registry, updates overlays/gitea
.github/workflows/build-and-push.yml   → ghcr.io,         updates overlays/github
k8s/base/                              deployment, service (NodePort 32041), ExternalSecret
k8s/base/argocd-app.yaml               tracks the Gitea repo — apply this one
k8s/base/argocd-app-github.yaml        or this one. Never both: they fight
```

`blog.krishnarajthadesar.in` points at NodePort **32041**.

### Vault

One record, `apps/humlog/humlog/env`, pulled in by
`k8s/base/externalsecret.yaml`. `.env.example` is the list of keys and what
each is for. The ones without which the app will not start or will not work:

```bash
vault kv put apps/humlog/humlog/env \
  DATABASE_URL="postgresql://..." \
  SECRET_KEY="$(openssl rand -hex 32)" \
  PUBLIC_BASE_URL="https://blog.krishnarajthadesar.in" \
  CORS_ORIGINS="https://humlog.vercel.app" \
  MINIO_ENDPOINT="..." MINIO_ACCESS_KEY="..." MINIO_SECRET_KEY="..." \
  MINIO_BUCKET="humlog" MINIO_REGION="us-east-1" \
  SMTP_HOST="smtp.gmail.com" SMTP_PORT="587" \
  SMTP_USER="you@gmail.com" SMTP_PASSWORD="the-16-char-app-password" \
  SMTP_FROM_NAME="hum.log"
```

`SMTP_PASSWORD` is a Google **app password**
(<https://myaccount.google.com/apppasswords>, needs 2-Step Verification on),
not the account password — Google will refuse that one. With SMTP unset the app
runs fine and only password resets stop working, which from the outside looks
exactly like them working, so the startup log complains about it in as many
words.

`SECRET_KEY` also salts the hash of commenters' IP addresses. Rotating it is
safe, and costs the rate-limit history and the "remove everything from this
commenter" links on comments left before the change.

Without `PUBLIC_BASE_URL`, exported posts get relative image links, which work
in a browser on the same origin and nowhere else — including in the file
somebody just downloaded.

---

## Layout

```
main.py                      app, logging, health checks
app/enums.py                 every enumerated value in one place
app/db/models.py             the schema, and why it is that shape
app/blocks/ordering.py       fractional indices — read this before touching order
app/blocks/tree.py           flat rows → the shape you read
app/services/block_service.py    every editing gesture. The heart of it
app/services/blog_service.py     posts, slugs, publishing
app/services/comment_service.py  anonymous comments and what keeps them survivable
app/services/export_service.py   markdown out, HTML out, sanitised
app/services/auth_service.py     passwords, tokens, reset codes
app/services/email_service.py    SMTP. Optional
app/services/storage_service.py  S3 via boto3
app/api/                     routes, schemas, serialisers, rate limits
tests/                       the rules above, written down as assertions
```
