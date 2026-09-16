# Building hum.log, a blog you have to type

Most writing apps are designed around one goal:

Make words arrive as quickly as possible.

Paste a draft from somewhere else. Import a document. Drop in generated text. Start from a template. Remove every bit of friction between having some text and putting it online.

I wanted to try the opposite.

**What changes when every sentence on a blog has to pass through a keyboard?**

That question became **hum.log**: a small publishing platform where text cannot be pasted. Not a paragraph, not a sentence, not a word. If something appears in a post, its author had to sit down and type it.

Images are the exception, because nobody should have to type a photograph.

## The rule is a constraint, not a security claim

The important thing to say first is that this is not an anti-cheating system and it is not an AI detector.

A determined person can call the API directly, automate keyboard events, or modify the client. A server receiving the string `hello` cannot prove whether it came from five physical keystrokes, a clipboard, a script, or a very patient robot. Pretending otherwise would produce an annoying security theatre layer rather than a trustworthy guarantee.

The point is to change the default.

On most websites, the cheapest path is:

```text
generate somewhere else
-> copy
-> paste
-> publish
```

On hum.log, the normal path is:

```text
think
-> type
-> read it back
-> edit
-> publish
```

That extra effort changes the shape of the writing. It tends to become shorter, more deliberate, and more obviously connected to the person who had to spend time entering it.

The rule is a ritual. The product is everything required to make that ritual pleasant enough to keep.

## Blocking paste without breaking typing

Stopping `Ctrl+V` is easy. Stopping pasted text without damaging normal input is much more interesting.

The browser guard works in layers.

The ordinary `paste` event catches keyboard shortcuts, context-menu paste, mobile paste bubbles, and middle-click paste on Linux. A native `beforeinput` listener catches insertions announced as `insertFromPaste`, `insertFromDrop`, or `insertFromYank`, including paths used by mobile browsers. A final burst check catches implausibly large `insertText` events from browsers that do not describe the source properly.

The exceptions matter as much as the blocks:

- autocorrect and spellcheck replacements still work
- Chinese, Japanese, Korean, and other composition-based input methods still work
- password managers can fill usernames, email addresses, codes, and passwords
- the search box accepts pasted text because searching is not publishing
- copying your own writing out is completely fine
- image files can be pasted or dropped into the editor

A rule that only works by making phone keyboards miserable is not a stronger rule. It is a worse editor.

When text is refused, the interface explains why instead of silently doing nothing. The first attempt to paste should feel like discovering the premise, not like finding a broken textarea.

## Making a no-paste editor bearable

If moving text required deleting and retyping whole sections, the idea would become tedious almost immediately.

So a post in hum.log is not one giant document field. It is a list of blocks.

```text
post
├── title, summary, cover, slug, status
└── blocks in reading order
    ├── text
    ├── image
    ├── code
    ├── table
    └── divider
```

Every paragraph is its own row. Pressing Enter creates the next block. Blocks can be reordered, nested, transformed, or deleted without rewriting the rest of the post.

The editor supports:

- normal paragraphs
- headings at three levels
- block quotes
- bulleted lists
- numbered lists that continue automatically
- fenced code blocks
- Markdown tables
- horizontal dividers
- images with typed captions

Typing `/` on an empty block opens the block menu. The same actions are available from a toolbar, so the feature is discoverable without knowing slash commands.

Tab nests a block under its previous sibling. Shift+Tab brings it back out. The nesting depth is capped so a post does not become an archaeological dig. Blocks can move with toolbar arrows, `Alt+Shift+↑` and `Alt+Shift+↓`, or a drag handle on screens large enough to use one comfortably.

Deleting a parent does not silently destroy everything under it. Its children are promoted into the gap unless a caller explicitly asks to remove the whole subtree.

The editor gives the constraint somewhere useful to go: you still have to type the words, but you never have to retype them merely to rearrange the page.

## Markdown while writing, the real post while reading

Block content is stored as Markdown.

When a block has focus, the editor shows the source. When focus leaves, it renders the result. A heading becomes a heading, emphasis becomes emphasis, a list gets its marker, and a table becomes a table without needing a separate preview screen.

That swap looks simple until a long paragraph wraps across four lines and the newly mounted textarea thinks it is only one line tall. The editor measures the input on mount, after every edit, and after a window resize so clicking a rendered block does not make half of it disappear.

Headings have the opposite problem. `# A title` is short Markdown source but renders much larger than normal text. The focused textarea adopts the same type size and line height as the rendered heading, keeping the page from jumping when the caret enters it.

Clicking rendered text also estimates the corresponding position in the Markdown source. It is exact for ordinary prose and close enough around formatting markers to avoid throwing the caret to the end every time.

This was a useful reminder that a “simple Markdown editor” is simple only until people start clicking inside it.

## Lists should behave like lists

A numbered-list button that inserts `1.` once is not really list support.

In hum.log, pressing Enter after `1. First item` creates `2.`. The next item becomes `3.`, and so on. Bulleted lists continue their chosen marker in the same way. Pressing Enter on an empty list item removes the marker and returns to normal text.

Each item remains an independent movable block. The familiar typing behaviour does not require turning the list into one opaque document fragment.

## Images are first-class blocks

An image can be selected from the block menu, pasted from the clipboard, or dropped onto the editor. Dropping it onto an empty text block replaces that block; dropping it elsewhere inserts a new image block below.

JPEG, PNG, GIF, WebP, and AVIF files are supported, with a size limit enforced before storage. SVG is deliberately excluded because an SVG is a document capable of executing code, not merely a harmless bitmap.

Every image can have a caption. The caption is visible to readers, becomes accessible alt text, and survives Markdown, HTML, and portable exports. An empty image block shows an explicit “Add a caption” action instead of hiding the feature behind an invisible focus state.

Cover images are separate from body images. They appear on the post itself and in feed cards, so the visual identity of a post is present before a reader opens it.

Draft images remain private. The editor requests a short-lived storage URL, then falls back to an authenticated API download if the browser cannot reach that URL. Published images use stable public URLs suitable for readers and caches.

## Saving is an explicit decision

The first editor saved text shortly after every keystroke. It worked, but it made editing feel strangely irreversible: the server changed before the writer had decided the edit was ready.

Now ordinary title and body edits stay local until the writer presses **Save** or uses `Ctrl+S` / `Cmd+S`.

The interface says when changes are unsaved, protects against accidental reloads and link navigation, and disables preview or export until the saved version matches what is on screen. Publishing saves pending edits first.

Structural gestures still remain responsive. A new block appears immediately, a drag lands immediately, and the network work is serialized behind the interaction. The page never waits for a round trip before letting the writer continue.

That creates a useful separation:

```text
typing        -> local draft state
Save          -> persisted content
rearranging   -> optimistic structural change
Publish       -> public lifecycle change
```

## Draft, publish, revise, repeat

A new post begins as an empty draft. Empty is treated as a normal state, not an error.

The writer can set:

- a title
- a short summary for feeds
- a cover image
- a readable URL slug
- whether comments are open

Slugs are unique per author, so two people can both publish a post called “On Mondays.” Renaming a draft updates its generated slug. Renaming a published post leaves the existing URL alone unless the writer deliberately changes it, because a slightly outdated slug is better than a broken link.

Publishing makes the post public. Unpublishing returns it to a private draft without deleting its comments. The original publication time stays stable, so fixing a typo does not push an old post back to the top of the feed.

The writing dashboard groups everything, drafts, and published posts into tabs and shows block counts, comment counts, dates, and summaries at a glance.

## Reading should stay quieter than writing

The public side is intentionally closer to a table of contents than a social-media timeline.

The feed shows cover images, titles, summaries, authors, publication dates, and comment counts. Readers can search titles, move through paginated results, open an author profile, and browse everything that person has published.

A post page adds reading time, its cover, the complete block tree, comments, and a portable download. View counts are registered by the backend, but an author rereading their own post does not inflate the number.

The visual system is deliberately restrained: warm paper in light mode, warm ink in dark mode, and one brass accent for the current or primary action. Newsreader is the default face for prose, while Inter is available as a sans-serif reading preference saved on the device. JetBrains Mono handles code and the wordmark.

The layout narrows for reading, widens for lists, and leaves a little gutter space around the editor for block controls. On a phone, the editor moves important controls into a fixed bottom toolbar instead of depending on hover.

## Comments without making everyone create an account

Anyone can comment on a published post. A name is optional, an account is unnecessary, and replies form one clear level of conversation rather than an endlessly shrinking tree.

Comments follow the same typing rule as posts. A reply is still writing, so pasted text is refused there too.

Open comments need abuse controls even when identity is intentionally light. The backend combines:

- request rate limits
- per-post hourly limits
- duplicate-comment detection
- a hidden honeypot field for form-filling bots
- a salted hash of the commenter address instead of storing the raw address
- author controls to hide, restore, delete, or sweep comments from one source

The address hash is enough to enforce limits and moderate a flood. It is not a readable record of who visited the post.

Writers can close comments per post. Existing conversations remain attached if a post is temporarily unpublished.

## Your writing should be able to leave

A platform built around deliberate writing should not trap that writing.

Every post can be copied or exported as Markdown or sanitized HTML. Flat Markdown blocks come out as the text the author typed. Nested blocks become nested list items because plain Markdown has no universal representation for “a paragraph under another paragraph.”

Images exposed the difference between an export that looks valid and one that actually travels. An absolute API URL can fail when a server moves, and a short-lived signed storage URL is already broken by the time somebody opens an old archive.

So downloads are portable ZIP files:

```text
my-post.zip
├── my-post.md        # or my-post.html
└── images/
    ├── <image uuid>.png
    └── <image uuid>.webp
```

The document uses relative `images/...` references and every referenced file is included. If an image cannot be fetched, the export fails clearly instead of producing an archive with a broken link.

HTML exports are complete standalone documents with readable styling. Both live rendering and export rendering are sanitized, because opening somebody else's post should never require trusting raw HTML they typed.

Copying out is allowed. The rule is about how words enter hum.log, not about claiming ownership over what happens to them afterwards.

## Accounts and the way back in

Writers can register with a username, email address, password, and optional display name. They can sign in with either username or email, edit their display name and bio, and sign out individual sessions.

Access tokens are short-lived and refreshed through rotating, revocable refresh sessions. Changing a password revokes every session.

Forgotten passwords use an eight-letter email code. Ambiguous characters such as `I`, `O`, `0`, and `1` are removed from the alphabet because somebody will read the code from one device and type it on another. Codes expire after fifteen minutes, only their hashes are stored, asking for another invalidates the old one, and repeated wrong attempts burn the code.

Password, email, username, and recovery-code fields allow password managers and clipboard input. The no-paste rule applies to published writing, not account security.

## The block model underneath it

The backend stores blocks as a tree, but sends them to the client as a flat depth-first list with a depth value.

Each block has a parent and a fractional `order_key`. A new key can be generated between two existing keys, so moving one paragraph does not require renumbering every paragraph after it.

```text
move block C between A and B
-> calculate one key between A and B
-> update C
-> leave every other row alone
```

Keys are meaningful among siblings. Tab changes the parent. Arrow movement swaps sibling subtrees. Dragging calculates a new parent and previous sibling. The backend validates depth and prevents a block from being dropped into its own descendants.

The frontend mirrors those rules for instant optimistic updates, while the server remains the final authority.

New blocks also need two identities. The UI must render and focus a block before the API has returned its UUID, so it gives the block a temporary React key. When the real UUID arrives, the stable UI key remains unchanged. Without that separation, a response from the server can remount the textarea under the writer's hands and move the caret.

Writes run through one promise chain. That lets “create this block, type into it, indent it, then save it” reach the server in the same order the person performed it.

These are small details, but block editors are made almost entirely of small details that become visible the moment one is wrong.

## The architecture

hum.log is split into a frontend and a backend.

The frontend uses React, Vite, Tailwind CSS, React Router, `dnd-kit`, Framer Motion, Marked, and DOMPurify. It is deployed as a PWA, with an installable manifest and cached application shell. API responses are never served from the service-worker cache because drafts, sessions, and comments must remain live. Updates wait for confirmation instead of reloading underneath an unfinished sentence.

The backend uses FastAPI, SQLAlchemy, Alembic, PostgreSQL, and S3-compatible object storage. It owns accounts, post lifecycle, block ordering, image privacy, exports, comments, moderation, rate limits, and view counts.

```text
browser
├── React editor and reader
├── no-paste input guard
└── local unsaved state
        |
        v
FastAPI
├── authentication and public routes
├── block and export services
└── comment controls
        |
        ├── PostgreSQL: users, posts, blocks, comments
        └── S3/MinIO: images
```

The frontend is deployed on Vercel. The API, database migrations, and object-storage integration run in containers on Kubernetes. Draft endpoints require authentication, while public routes are deliberately separate and never reveal whether an unpublished post exists.

## What I learned

The no-paste rule was one line. Almost none of the engineering was.

The difficult work was in preserving all the things a normal editor quietly provides: mobile input, composition, autocorrect, stable focus, list continuation, visible captions, responsive controls, safe navigation, private draft images, readable URLs, comments, and exports that still work after leaving the site.

It also reinforced a useful distinction between a product constraint and a security boundary. The browser can make typing the honest default. It cannot prove human keystrokes to a server, and the project is better for saying that plainly.

Most importantly, the constraint did not lead to a textarea with paste disabled. It led to a different model of writing: small movable blocks, explicit saves, quiet reading pages, and an export path designed around ownership.

The fastest possible way to publish text already exists in a thousand places.

hum.log is an experiment in making the slower way feel worthwhile.

*Try it at [humlog.vercel.app](https://humlog.vercel.app/). The source is split between the [frontend](https://git.krishnarajthadesar.in/krishnaraj/humlog_ui) and [backend](https://git.krishnarajthadesar.in/krishnaraj/humlog) repositories.*
