"""End to end, over HTTP: register, write, publish, comment, export."""

import io
import zipfile

import pytest


@pytest.fixture()
def post(client, author):
    """A post with three blocks in it, ready to publish."""
    blog = client.post("/api/blogs", json={"title": "Typing is the point"}).json()
    for line in ("# Why type", "Because it is slower.", "And slower is the point."):
        client.post(f"/api/blogs/{blog['blog_uuid']}/blocks", json={"content": line})
    return blog


def blocks_of(client, blog_uuid):
    response = client.get(f"/api/blogs/{blog_uuid}/blocks")
    assert response.status_code == 200, response.text
    return response.json()


# --- accounts --------------------------------------------------------------

def test_a_duplicate_email_is_refused(client, author):
    response = client.post(
        "/api/auth/register",
        json={
            "username": "someone-else",
            "email": "writer@example.com",
            "password": "x",
            "display_name": "Someone",
        },
    )
    assert response.status_code == 409


def test_signing_in_works_with_a_username_or_an_email(client, author):
    for identifier in ("writer", "writer@example.com"):
        response = client.post(
            "/api/auth/login", json={"identifier": identifier, "password": "hunter2"}
        )
        assert response.status_code == 200, identifier


def test_a_wrong_password_is_a_401(client, author):
    response = client.post(
        "/api/auth/login", json={"identifier": "writer", "password": "nope"}
    )
    assert response.status_code == 401


def test_forgot_password_says_the_same_thing_either_way(client, author):
    known = client.post("/api/auth/forgot-password", json={"email": "writer@example.com"})
    unknown = client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()


def test_a_reset_code_works_once(client, author, db):
    """The code is checked against the hash, spent, and then gone."""
    from app.db.models import User
    from app.services import auth_service

    user = db.query(User).filter(User.username == "writer").first()
    code = auth_service.issue_reset_code(db, user)

    payload = {"email": "writer@example.com", "code": code, "new_password": "typed-it"}
    assert client.post("/api/auth/reset-password", json=payload).status_code == 200
    assert client.post("/api/auth/reset-password", json=payload).status_code == 400

    signed_in = client.post(
        "/api/auth/login", json={"identifier": "writer", "password": "typed-it"}
    )
    assert signed_in.status_code == 200


def test_a_wrong_reset_code_is_refused(client, author, db):
    from app.db.models import User
    from app.services import auth_service

    user = db.query(User).filter(User.username == "writer").first()
    auth_service.issue_reset_code(db, user)
    response = client.post(
        "/api/auth/reset-password",
        json={"email": "writer@example.com", "code": "AAAAAAAA", "new_password": "x"},
    )
    assert response.status_code == 400


# --- writing ---------------------------------------------------------------

def test_a_new_post_starts_empty(client, author):
    blog = client.post("/api/blogs", json={"title": "Nothing yet"}).json()
    assert blog["block_count"] == 0
    assert blog["status"] == "draft"
    assert blog["slug"] == "nothing-yet"
    assert blocks_of(client, blog["blog_uuid"]) == []


def test_a_post_reads_back_whole(client, post):
    """The author's own view: the post, its blocks, and their depths."""
    response = client.get(f"/api/blogs/{post['blog_uuid']}")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["title"] == "Typing is the point"
    assert payload["author"]["username"] == "writer"
    assert payload["block_count"] == 3
    assert [b["depth"] for b in payload["blocks"]] == [0, 0, 0]


def test_typing_lines_builds_a_post(client, post):
    contents = [b["content"] for b in blocks_of(client, post["blog_uuid"])]
    assert contents == ["# Why type", "Because it is slower.", "And slower is the point."]


def test_a_multiline_body_comes_back_as_several_blocks(client, author):
    blog = client.post("/api/blogs", json={"title": "Lines"}).json()
    response = client.post(
        f"/api/blogs/{blog['blog_uuid']}/blocks", json={"content": "one\ntwo"}
    )
    assert response.status_code == 201
    assert [b["content"] for b in response.json()] == ["one", "two"]


def test_moving_a_block_down_reorders_the_post(client, post):
    blocks = blocks_of(client, post["blog_uuid"])
    first = blocks[0]["block_uuid"]
    response = client.post(
        f"/api/blogs/{post['blog_uuid']}/blocks/{first}/move", json={"direction": "down"}
    )
    assert response.status_code == 200
    contents = [b["content"] for b in blocks_of(client, post["blog_uuid"])]
    assert contents[:2] == ["Because it is slower.", "# Why type"]


def test_indenting_a_block_reports_its_new_depth(client, post):
    blocks = blocks_of(client, post["blog_uuid"])
    second = blocks[1]["block_uuid"]
    response = client.post(f"/api/blogs/{post['blog_uuid']}/blocks/{second}/indent")
    assert response.status_code == 200
    assert response.json()["depth"] == 1
    assert response.json()["parent_block_uuid"] == blocks[0]["block_uuid"]


def test_indenting_the_first_block_is_a_400(client, post):
    first = blocks_of(client, post["blog_uuid"])[0]["block_uuid"]
    response = client.post(f"/api/blogs/{post['blog_uuid']}/blocks/{first}/indent")
    assert response.status_code == 400


def test_deleting_a_block_removes_it(client, post):
    blocks = blocks_of(client, post["blog_uuid"])
    target = blocks[1]["block_uuid"]
    assert client.delete(
        f"/api/blogs/{post['blog_uuid']}/blocks/{target}"
    ).status_code == 204
    assert len(blocks_of(client, post["blog_uuid"])) == len(blocks) - 1


def test_another_writers_post_is_not_reachable(client, post, db):
    """Knowing a uuid is not enough. The path is scoped to its owner."""
    other = client.post(
        "/api/auth/register",
        json={
            "username": "intruder",
            "email": "intruder@example.com",
            "password": "x",
            "display_name": "Intruder",
        },
    ).json()
    client.headers["Authorization"] = f"Bearer {other['access_token']}"

    assert client.get(f"/api/blogs/{post['blog_uuid']}").status_code == 404
    assert client.post(
        f"/api/blogs/{post['blog_uuid']}/blocks", json={"content": "mine now"}
    ).status_code == 404


# --- publishing and reading ------------------------------------------------

def test_an_empty_post_cannot_be_published(client, author):
    blog = client.post("/api/blogs", json={"title": "Nothing"}).json()
    response = client.post(f"/api/blogs/{blog['blog_uuid']}/publish")
    assert response.status_code == 400


def test_a_draft_is_invisible_to_readers(client, post):
    anonymous = dict(client.headers)
    client.headers.pop("Authorization")
    assert client.get(f"/api/public/blogs/{post['blog_uuid']}").status_code == 404
    assert client.get("/api/public/blogs").json()["total"] == 0
    client.headers.update(anonymous)


def test_publishing_puts_a_post_in_the_feed(client, post):
    assert client.post(f"/api/blogs/{post['blog_uuid']}/publish").status_code == 200
    client.headers.pop("Authorization")

    feed = client.get("/api/public/blogs").json()
    assert feed["total"] == 1
    assert feed["blogs"][0]["title"] == "Typing is the point"
    assert feed["blogs"][0]["author"]["username"] == "writer"
    # No email anywhere in a public response.
    assert "email" not in feed["blogs"][0]["author"]


def test_a_published_post_reads_by_uuid(client, post):
    client.post(f"/api/blogs/{post['blog_uuid']}/publish")
    client.headers.pop("Authorization")

    response = client.get(f"/api/public/blogs/{post['blog_uuid']}")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert [b["content"] for b in payload["blocks"]][0] == "# Why type"
    assert payload["author"]["display_name"] == "A Writer"


def test_a_published_post_reads_by_slug(client, post):
    client.post(f"/api/blogs/{post['blog_uuid']}/publish")
    client.headers.pop("Authorization")

    response = client.get(f"/api/public/posts/writer/{post['slug']}")
    assert response.status_code == 200
    assert len(response.json()["blocks"]) == 3


# --- comments --------------------------------------------------------------

def test_comments_need_a_published_post(client, post):
    client.headers.pop("Authorization")
    response = client.post(
        f"/api/public/blogs/{post['blog_uuid']}/comments", json={"body": "hello"}
    )
    assert response.status_code == 404


def test_anyone_can_comment_on_a_published_post(client, post):
    client.post(f"/api/blogs/{post['blog_uuid']}/publish")
    client.headers.pop("Authorization")

    response = client.post(
        f"/api/public/blogs/{post['blog_uuid']}/comments",
        json={"body": "I typed this.", "author_name": "A Reader"},
    )
    assert response.status_code == 201
    assert response.json()["author_name"] == "A Reader"

    listed = client.get(f"/api/public/blogs/{post['blog_uuid']}/comments").json()
    assert [c["body"] for c in listed] == ["I typed this."]


def test_the_same_comment_twice_is_refused(client, post):
    client.post(f"/api/blogs/{post['blog_uuid']}/publish")
    client.headers.pop("Authorization")

    body = {"body": "double tap"}
    assert client.post(f"/api/public/blogs/{post['blog_uuid']}/comments", json=body).status_code == 201
    assert client.post(f"/api/public/blogs/{post['blog_uuid']}/comments", json=body).status_code == 429


def test_the_honeypot_swallows_a_bot_without_telling_it(client, post):
    client.post(f"/api/blogs/{post['blog_uuid']}/publish")
    client.headers.pop("Authorization")

    response = client.post(
        f"/api/public/blogs/{post['blog_uuid']}/comments",
        json={"body": "buy things", "website": "http://spam.example"},
    )
    assert response.status_code == 201
    assert client.get(f"/api/public/blogs/{post['blog_uuid']}/comments").json() == []


def test_comments_can_be_switched_off(client, post):
    client.post(f"/api/blogs/{post['blog_uuid']}/publish")
    client.patch(f"/api/blogs/{post['blog_uuid']}", json={"comments_enabled": False})
    client.headers.pop("Authorization")

    response = client.post(
        f"/api/public/blogs/{post['blog_uuid']}/comments", json={"body": "hello?"}
    )
    assert response.status_code == 400


def test_an_author_can_hide_a_comment(client, post):
    client.post(f"/api/blogs/{post['blog_uuid']}/publish")
    token = client.headers["Authorization"]

    client.headers.pop("Authorization")
    comment = client.post(
        f"/api/public/blogs/{post['blog_uuid']}/comments", json={"body": "rude"}
    ).json()

    client.headers["Authorization"] = token
    response = client.patch(
        f"/api/blogs/{post['blog_uuid']}/comments/{comment['comment_uuid']}",
        json={"status": "hidden"},
    )
    assert response.status_code == 200

    client.headers.pop("Authorization")
    assert client.get(f"/api/public/blogs/{post['blog_uuid']}/comments").json() == []


# --- export ----------------------------------------------------------------

def test_exporting_markdown(client, post):
    response = client.get(f"/api/blogs/{post['blog_uuid']}/export")
    assert response.status_code == 200
    payload = response.json()
    assert payload["format"] == "markdown"
    assert payload["filename"] == "typing-is-the-point.md"
    assert "Because it is slower." in payload["content"]


def test_exporting_html_as_a_download(client, post):
    response = client.get(
        f"/api/blogs/{post['blog_uuid']}/export", params={"format": "html", "download": True}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "typing-is-the-point.html" in response.headers["content-disposition"]
    assert "<h1>Why type</h1>" in response.text


def test_exporting_a_portable_zip(client, post):
    response = client.get(
        f"/api/blogs/{post['blog_uuid']}/export",
        params={"format": "markdown", "bundle": True, "download": True},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "typing-is-the-point.zip" in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert "typing-is-the-point.md" in archive.namelist()


def test_a_reader_can_export_a_published_post(client, post):
    client.post(f"/api/blogs/{post['blog_uuid']}/publish")
    client.headers.pop("Authorization")
    response = client.get(f"/api/public/blogs/{post['blog_uuid']}/export")
    assert response.status_code == 200
    assert "Because it is slower." in response.json()["content"]


def test_a_reader_cannot_export_a_draft(client, post):
    client.headers.pop("Authorization")
    assert client.get(f"/api/public/blogs/{post['blog_uuid']}/export").status_code == 404
