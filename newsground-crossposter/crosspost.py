#!/usr/bin/env python3
"""Mirror newly authored X posts to Bluesky and Mastodon.

On the first run, records the newest X post and does NOT import old posts.
Subsequent runs process new posts oldest-first, with per-platform state.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # GitHub Actions uses real environment variables.

LOG = logging.getLogger("crossposter")
X_API = "https://api.x.com/2"
STATE_PATH = Path(os.getenv("STATE_PATH", "state.json"))
MAX_PAGES = 20


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing {name}. Add it as a GitHub Actions secret (or to .env locally).")
    return value


def request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    """Perform an HTTP request and explain failures without exposing secrets."""
    try:
        response = requests.request(method, url, timeout=40, **kwargs)
        response.raise_for_status()
        return response.json()
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else "unknown"
        # Don't log the entire body: API failures sometimes include sensitive data.
        raise RuntimeError(f"{method} {urlparse(url).hostname} failed with HTTP {code}") from exc
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError(f"{method} {urlparse(url).hostname} failed: {type(exc).__name__}") from exc


def x_post_url(post_id: str) -> str:
    return f"https://x.com/i/status/{post_id}"


def expand_x_urls(text: str, entities: dict[str, Any] | None, has_media: bool) -> str:
    """Expand t.co URLs. Remove an appended photo/video t.co when images are attached."""
    for url in (entities or {}).get("urls", []):
        short = url.get("url", "")
        expanded = url.get("unwound_url") or url.get("expanded_url") or short
        display = url.get("display_url", "")
        is_media_link = display.startswith("pic.twitter.com/") or bool(re.search(r"/(photo|video)/\d+$", expanded))
        if short:
            replacement = "" if has_media and is_media_link else expanded
            text = text.replace(short, replacement)
    return re.sub(r"[ \t]+\n", "\n", text).strip()


def trim_for_platform(text: str, post_id: str, max_chars: int, max_bytes: int | None = None) -> str:
    """Preserve short posts, or link back when the target has a smaller limit."""
    import regex
    chars = regex.findall(r"\X", text)
    if len(chars) <= max_chars and (max_bytes is None or len(text.encode("utf-8")) <= max_bytes):
        return text
    suffix = f"…\n\nFull post on X: {x_post_url(post_id)}"
    kept: list[str] = []
    for grapheme in chars:
        candidate = "".join(kept) + grapheme + suffix
        if len(kept) + 1 + len(regex.findall(r"\X", suffix)) > max_chars:
            break
        if max_bytes and len(candidate.encode("utf-8")) > max_bytes:
            break
        kept.append(grapheme)
    return "".join(kept).rstrip() + suffix


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"initialized": False, "last_seen_id": None, "posts": {}, "x_user_id": None}
    with STATE_PATH.open(encoding="utf-8") as f:
        state = json.load(f)
    return {
        "initialized": bool(state.get("initialized", False)),
        "last_seen_id": state.get("last_seen_id"),
        "posts": state.get("posts", {}),
        "x_user_id": state.get("x_user_id"),
    }


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(STATE_PATH)


class XClient:
    def __init__(self, bearer: str, username: str, user_id: str = ""):
        self.headers = {"Authorization": f"Bearer {bearer}"}
        self.username = username.lstrip("@")
        self.user_id = user_id

    def resolve_user(self) -> str:
        if not self.user_id:
            response = request_json(
                "GET", f"{X_API}/users/by/username/{self.username}", headers=self.headers
            )
            self.user_id = response["data"]["id"]
        return self.user_id

    def fetch(self, since_id: str | None = None) -> list[dict[str, Any]]:
        """Read all available newer posts, including own-thread replies, excluding reposts.

        Intentionally refuses to advance the checkpoint if pagination is truncated.
        """
        user_id = self.resolve_user()
        params: dict[str, Any] = {
            # First run is an initialization only. Fetch the newest five posts
            # rather than backfilling the entire history (which costs X API reads).
            "max_results": 5 if not since_id else 100,
            "exclude": "retweets",
            "tweet.fields": "author_id,created_at,entities,attachments,referenced_tweets,in_reply_to_user_id,note_tweet",
            "expansions": "attachments.media_keys",
            "media.fields": "media_key,type,url,preview_image_url,alt_text",
        }
        if since_id and since_id != "0":
            params["since_id"] = since_id
        all_posts: list[dict[str, Any]] = []
        for _ in range(MAX_PAGES):
            response = request_json(
                "GET", f"{X_API}/users/{user_id}/tweets", params=params, headers=self.headers
            )
            media_map = {
                m["media_key"]: m for m in response.get("includes", {}).get("media", [])
            }
            for item in response.get("data", []):
                if item.get("author_id") and item["author_id"] != user_id:
                    raise RuntimeError("X returned an unexpected author. Stopped for safety.")
                images: list[dict[str, Any]] = []
                video = False
                for key in item.get("attachments", {}).get("media_keys", []):
                    media = media_map.get(key, {})
                    if media.get("type") == "photo":
                        if not media.get("url"):
                            raise RuntimeError(
                                f"Photo URL missing for X post {item['id']}; not silently dropping the image."
                            )
                        images.append({"url": media["url"], "alt": media.get("alt_text") or ""})
                    elif media.get("type") in ("video", "animated_gif"):
                        video = True
                    elif not media:
                        # Important: missing media metadata should not silently lose content.
                        raise RuntimeError(f"X did not return media details for post {item['id']}")
                parent_id = None
                is_repost = False
                for ref in item.get("referenced_tweets", []):
                    if ref.get("type") == "replied_to":
                        parent_id = ref.get("id")
                    if ref.get("type") == "retweeted":
                        is_repost = True
                # X Premium long-form posts expose their full body in note_tweet.
                note = item.get("note_tweet") or {}
                text = expand_x_urls(
                    note.get("text") or item.get("text", ""),
                    note.get("entities") or item.get("entities"),
                    bool(images or video),
                )
                if video:
                    text = (text + f"\n\nVideo: {x_post_url(item['id'])}").strip()
                all_posts.append({
                    "id": str(item["id"]),
                    "text": text,
                    "images": images,
                    "parent_id": parent_id,
                    "reply_to_user_id": item.get("in_reply_to_user_id"),
                    "is_repost": is_repost,
                })
            # The initialization only needs the newest post ID.
            # Never paginate old history and incur unnecessary X API charges.
            if not since_id:
                break
            token = response.get("meta", {}).get("next_token")
            if not token:
                break
            params["pagination_token"] = token
        else:
            raise RuntimeError("More than 2,000 new X posts; stopped to avoid losing the timeline. Increase MAX_PAGES.")
        return sorted(all_posts, key=lambda p: int(p["id"]))


def image_bytes(source_url: str) -> bytes:
    """Fetch X-hosted image and fit Bluesky's conservative ~1MB image limit."""
    from PIL import Image, ImageOps
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or parsed.hostname != "pbs.twimg.com":
        raise RuntimeError("Image URL is not an HTTPS pbs.twimg.com link; refusing unsafe download")
    try:
        response = requests.get(source_url, timeout=40, stream=True, allow_redirects=False)
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_content(chunk_size=65536):
            content.extend(chunk)
            if len(content) > 16 * 1024 * 1024:
                raise RuntimeError("Image exceeds 16 MB download limit")
        with Image.open(BytesIO(content)) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((2000, 2000))
            if img.mode != "RGB":
                # Blend transparency onto white rather than producing a black background.
                rgba = img.convert("RGBA")
                white = Image.new("RGBA", img.size, (255, 255, 255, 255))
                white.alpha_composite(rgba)
                img = white.convert("RGB")
            for quality in (90, 82, 73, 63, 50):
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=quality, optimize=True)
                if len(buf.getvalue()) < 950_000:
                    return buf.getvalue()
            for max_dim in (1400, 1000, 800):
                resized = img.copy()
                resized.thumbnail((max_dim, max_dim))
                buf = BytesIO()
                resized.save(buf, format="JPEG", quality=65, optimize=True)
                if len(buf.getvalue()) < 950_000:
                    return buf.getvalue()
        raise RuntimeError("Could not compress image under Bluesky's size limit")
    except (requests.RequestException, OSError, ValueError) as exc:
        raise RuntimeError("Could not download/process attached image from X") from exc
    finally:
        try:
            response.close()  # type: ignore[possibly-undefined]
        except UnboundLocalError:
            pass


class BlueskyPublisher:
    def __init__(self, handle: str, app_password: str):
        # Import only when actually configured, so unit tests are network-free.
        from atproto import Client
        self.client = Client()
        self.client.login(handle, app_password)

    def publish(self, post: dict[str, Any], parent: dict[str, Any] | None) -> dict[str, Any]:
        from atproto import models
        text = trim_for_platform(post["text"], post["id"], 300, 3000)
        reply = None
        root = None
        if parent:
            root = parent.get("root") or {"uri": parent["uri"], "cid": parent["cid"]}
            reply = models.AppBskyFeedPost.ReplyRef(
                root=models.ComAtprotoRepoStrongRef.Main(**root),
                parent=models.ComAtprotoRepoStrongRef.Main(uri=parent["uri"], cid=parent["cid"]),
            )
        facets = self.client.detect_facets(text)
        if post["images"]:
            pictures = post["images"][:4]
            result = self.client.send_images(
                text=text,
                images=[image_bytes(p["url"]) for p in pictures],
                image_alts=[p["alt"] for p in pictures],
                reply_to=reply,
                facets=facets,
            )
        else:
            result = self.client.send_post(text=text, reply_to=reply, facets=facets)
        return {"uri": result.uri, "cid": result.cid, "root": root or {"uri": result.uri, "cid": result.cid}}


class MastodonPublisher:
    def __init__(self, server_url: str, token: str, max_chars: int = 500):
        self.server = server_url.rstrip("/")
        parsed = urlparse(self.server)
        if parsed.scheme != "https" or not parsed.hostname:
            raise RuntimeError("MASTODON_BASE_URL must be an HTTPS URL, such as https://mastodon.social")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.max_chars = max_chars

    def _upload_photo(self, url: str, alt: str) -> str:
        img = image_bytes(url)
        try:
            response = requests.post(
                f"{self.server}/api/v2/media",
                headers=self.headers,
                files={"file": ("crosspost.jpg", img, "image/jpeg")},
                data={"description": alt},
                timeout=60,
            )
            response.raise_for_status()
            payload = response.json()
            media_id = str(payload["id"])
        except (requests.RequestException, KeyError, ValueError) as exc:
            raise RuntimeError("Mastodon image upload failed") from exc
        if response.status_code == 202:
            for _ in range(12):
                time.sleep(5)
                data = request_json("GET", f"{self.server}/api/v1/media/{media_id}", headers=self.headers)
                if data.get("url"):
                    break
            else:
                raise RuntimeError("Mastodon image did not finish processing in time")
        return media_id

    def publish(self, post: dict[str, Any], parent: dict[str, Any] | None) -> dict[str, Any]:
        text = trim_for_platform(post["text"], post["id"], self.max_chars)
        form: list[tuple[str, str]] = [("status", text), ("visibility", "public")]
        if parent:
            form.append(("in_reply_to_id", str(parent["id"])))
        for pic in post["images"][:4]:
            form.append(("media_ids[]", self._upload_photo(pic["url"], pic["alt"])))
        headers = {**self.headers, "Idempotency-Key": f"x-mirror-{post['id']}"}
        result = request_json("POST", f"{self.server}/api/v1/statuses", headers=headers, data=form)
        return {"id": str(result["id"]), "url": result.get("url", "")}


class Processor:
    def __init__(self, x: Any, bluesky: Any, mastodon: Any, state: dict[str, Any], saver=save_state):
        self.x = x
        self.bluesky = bluesky
        self.mastodon = mastodon
        self.state = state
        self.saver = saver

    def run(self) -> int:
        # Cache the X account ID in state to avoid a billable user lookup each run.
        saved_uid = self.state.get("x_user_id")
        if saved_uid and not getattr(self.x, "user_id", None):
            self.x.user_id = saved_uid
        uid = self.x.resolve_user()
        if self.state.get("x_user_id") != uid:
            self.state["x_user_id"] = uid
            self.saver(self.state)
        posts = self.x.fetch(self.state.get("last_seen_id"))
        if not self.state["initialized"]:
            self.state["initialized"] = True
            self.state["last_seen_id"] = posts[-1]["id"] if posts else "0"
            self.saver(self.state)
            LOG.info("Initialized at X post %s. Old posts were NOT published.", self.state["last_seen_id"])
            return 0
        LOG.info("Found %d newer posts on X", len(posts))
        published = 0
        for post in posts:
            pid = post["id"]
            if self.state.get("last_seen_id") and int(pid) <= int(self.state["last_seen_id"]):
                continue
            # Avoid cross-posting conversations with other people or reposts.
            if post.get("is_repost") or (post.get("reply_to_user_id") and str(post["reply_to_user_id"]) != str(self.x.resolve_user())):
                LOG.info("Skipping repost or reply to another user: %s", pid)
                self.state["last_seen_id"] = pid
                self.saver(self.state)
                continue
            entry = self.state["posts"].setdefault(pid, {})
            parent_id = post.get("parent_id")
            parent = self.state["posts"].get(parent_id, {}) if parent_id else {}
            if "bluesky" not in entry:
                entry["bluesky"] = self.bluesky.publish(post, parent.get("bluesky"))
                self.saver(self.state)  # Preserve success if Mastodon subsequently fails.
                LOG.info("Published X %s on Bluesky", pid)
            if "mastodon" not in entry:
                entry["mastodon"] = self.mastodon.publish(post, parent.get("mastodon"))
                self.saver(self.state)
                LOG.info("Published X %s on Mastodon", pid)
            self.state["last_seen_id"] = pid
            self.saver(self.state)
            published += 1
        # Keep recent records to preserve reply chains without bloating the repository.
        if len(self.state["posts"]) > 2000:
            keep = sorted(self.state["posts"], key=int)[-2000:]
            self.state["posts"] = {k: self.state["posts"][k] for k in keep}
            self.saver(self.state)
        LOG.info("Synchronized %d posts", published)
        return published


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        x = XClient(required("X_BEARER_TOKEN"), required("X_USERNAME"), os.getenv("X_USER_ID", ""))
        state = load_state()
        bluesky = BlueskyPublisher(required("BLUESKY_HANDLE"), required("BLUESKY_APP_PASSWORD"))
        mastodon = MastodonPublisher(
            required("MASTODON_BASE_URL"), required("MASTODON_ACCESS_TOKEN"),
            int(os.getenv("MASTODON_MAX_CHARS", "500")),
        )
        Processor(x, bluesky, mastodon, state).run()
        return 0
    except Exception as exc:
        LOG.error("Crossposting stopped: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
