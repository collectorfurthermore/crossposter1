#!/usr/bin/env python3
"""Republish new original X posts to Bluesky and Mastodon.

First execution records a checkpoint without publishing historic X posts.
Never put credentials in this repository; use GitHub Actions secrets.
"""
import json
import logging
import os
import re
import sys
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import requests

LOG = logging.getLogger("crossposter")
STATE = Path("state.json")
API = "https://api.x.com/2"


def required(key):
    value = os.environ.get(key, "").strip()
    if not value:
        raise RuntimeError("Missing " + key + " (configure a GitHub Actions secret)")
    return value


def api(method, url, **kwargs):
    try:
        r = requests.request(method, url, timeout=40, **kwargs)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        raise RuntimeError(f"{method} {urlparse(url).hostname} returned HTTP {e.response.status_code}") from e
    except (requests.RequestException, ValueError) as e:
        raise RuntimeError(f"{method} {urlparse(url).hostname} request failed") from e


def load():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"initialized": False, "last_seen_id": None, "x_user_id": None, "posts": {}}


def save(state):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(STATE)


def x_link(pid):
    return f"https://x.com/i/status/{pid}"


def fits_bluesky(text):
    """Check Bluesky's 300-grapheme and 3000 UTF-8 byte limits."""
    import regex
    return len(regex.findall(r"\X", text)) <= 300 and len(text.encode("utf-8")) <= 3000


def fits_mastodon(text):
    """Skip posts longer than 500 characters; never truncate them."""
    return len(text) <= 500


class X:
    def __init__(self, username, token, uid=None):
        self.username = username.lstrip("@")
        self.uid = uid
        self.headers = {"Authorization": "Bearer " + token}

    def user_id(self):
        if not self.uid:
            self.uid = api("GET", f"{API}/users/by/username/{self.username}", headers=self.headers)["data"]["id"]
        return self.uid

    def new_posts(self, after=None):
        uid = self.user_id()
        params = {
            "max_results": 100 if after and after != "0" else 5,
            "exclude": "retweets",
            "tweet.fields": "author_id,created_at,entities,attachments,referenced_tweets,in_reply_to_user_id,note_tweet",
            "expansions": "attachments.media_keys",
            "media.fields": "media_key,type,url,preview_image_url,alt_text",
        }
        if after and after != "0":
            params["since_id"] = after
        found = []
        for _ in range(20):
            result = api("GET", f"{API}/users/{uid}/tweets", headers=self.headers, params=params)
            media = {m["media_key"]: m for m in result.get("includes", {}).get("media", [])}
            for p in result.get("data", []):
                if p.get("author_id") and str(p["author_id"]) != str(uid):
                    raise RuntimeError("Unexpected X author; refusing to crosspost")
                images, video = [], False
                for key in p.get("attachments", {}).get("media_keys", []):
                    m = media.get(key)
                    if not m:
                        raise RuntimeError("Missing X media metadata for " + p["id"])
                    if m["type"] == "photo":
                        if not m.get("url"):
                            raise RuntimeError("Missing image URL for " + p["id"])
                        images.append({"url": m["url"], "alt": m.get("alt_text", "")})
                    elif m["type"] in ("video", "animated_gif"):
                        video = True
                refs = p.get("referenced_tweets", [])
                parent = next((r["id"] for r in refs if r["type"] == "replied_to"), None)
                note = p.get("note_tweet") or {}
                txt = note.get("text") or p.get("text", "")
                for u in (note.get("entities") or p.get("entities") or {}).get("urls", []):
                    short = u.get("url", "")
                    target = u.get("unwound_url") or u.get("expanded_url") or short
                    display = u.get("display_url", "").lower()
                    parsed = urlparse(target)
                    is_native_media_link = (
                        display.startswith(("pic.twitter.com/", "pic.x.com/"))
                        or (
                            parsed.hostname in ("x.com", "www.x.com", "twitter.com", "www.twitter.com")
                            and re.search(r"/status/" + re.escape(str(p["id"])) + r"/(photo|video)/[0-9]+/?$", parsed.path, re.I)
                        )
                    )
                    if short:
                        txt = txt.replace(short, "" if is_native_media_link and (images or video) else target)
                txt = txt.strip()
                if video:
                    txt += f"\n\nVideo: {x_link(p['id'])}"
                found.append({"id": str(p["id"]), "text": txt, "images": images,
                              "parent": parent, "reply_user": p.get("in_reply_to_user_id"),
                              "repost": any(r.get("type") == "retweeted" for r in refs),
                              "quote": any(r.get("type") == "quoted" for r in refs)})
            if not after or after == "0":
                break
            token = result.get("meta", {}).get("next_token")
            if not token:
                break
            params["pagination_token"] = token
        else:
            raise RuntimeError("More than 2000 new posts; refusing to skip any")
        return sorted(found, key=lambda item: int(item["id"]))


def download_image(url):
    from PIL import Image, ImageOps
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "pbs.twimg.com":
        raise RuntimeError("Refusing image from unapproved host")
    r = requests.get(url, stream=True, timeout=40, allow_redirects=False)
    try:
        r.raise_for_status()
        chunks = bytearray()
        for chunk in r.iter_content(65536):
            chunks.extend(chunk)
            if len(chunks) > 16 * 1024 * 1024:
                raise RuntimeError("X image too large")
        with Image.open(BytesIO(chunks)) as raw:
            img = ImageOps.exif_transpose(raw)
            img.thumbnail((2000, 2000))
            if img.mode != "RGB":
                rgba = img.convert("RGBA")
                bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
                bg.alpha_composite(rgba)
                img = bg.convert("RGB")
            for size in (2000, 1400, 1000, 800):
                copy = img.copy()
                copy.thumbnail((size, size))
                for quality in (88, 75, 60, 45):
                    buf = BytesIO()
                    copy.save(buf, "JPEG", quality=quality, optimize=True)
                    if len(buf.getvalue()) <= 950000:
                        return buf.getvalue()
        raise RuntimeError("Cannot compress photo within Bluesky upload limits")
    finally:
        r.close()


class Bluesky:
    def __init__(self, handle, password):
        from atproto import Client
        self.client = Client()
        self.client.login(handle, password)

    def publish(self, post, parent=None):
        from atproto import models
        text = post["text"]
        if not fits_bluesky(text):
            raise ValueError("Post exceeds Bluesky length limits")
        reply = None
        root = None
        if parent:
            root = parent.get("root") or {"uri": parent["uri"], "cid": parent["cid"]}
            reply = models.AppBskyFeedPost.ReplyRef(
                root=models.ComAtprotoRepoStrongRef.Main(**root),
                parent=models.ComAtprotoRepoStrongRef.Main(uri=parent["uri"], cid=parent["cid"]))
        facets = self.client.detect_facets(text)
        if post["images"]:
            photos = post["images"][:4]
            sent = self.client.send_images(
                text=text, images=[download_image(p["url"]) for p in photos],
                image_alts=[p["alt"] for p in photos],
                reply_to=reply, facets=facets)
        else:
            sent = self.client.send_post(text=text, reply_to=reply, facets=facets)
        return {"uri": sent.uri, "cid": sent.cid,
                "root": root or {"uri": sent.uri, "cid": sent.cid}}


class Mastodon:
    def __init__(self, base_url, token):
        self.base = base_url.rstrip("/")
        if urlparse(self.base).scheme != "https":
            raise RuntimeError("MASTODON_BASE_URL must use https")
        self.auth = {"Authorization": "Bearer " + token}

    def photo(self, picture):
        r = requests.post(
            self.base + "/api/v2/media", headers=self.auth,
            files={"file": ("crosspost.jpg", download_image(picture["url"]), "image/jpeg")},
            data={"description": picture["alt"]}, timeout=60)
        try:
            r.raise_for_status()
            media_id = str(r.json()["id"])
            if r.status_code == 202:
                for _ in range(12):
                    time.sleep(5)
                    if api("GET", self.base + "/api/v1/media/" + media_id,
                           headers=self.auth).get("url"):
                        break
                else:
                    raise RuntimeError("Mastodon photo processing timed out")
            return media_id
        finally:
            r.close()

    def publish(self, post, parent=None):
        if not fits_mastodon(post["text"]):
            raise ValueError("Post exceeds Mastodon's 500-character limit")
        data = [("status", post["text"]),
                ("visibility", "public")]
        if parent:
            data.append(("in_reply_to_id", str(parent["id"])))
        for image in post["images"][:4]:
            data.append(("media_ids[]", self.photo(image)))
        headers = {**self.auth, "Idempotency-Key": "newsground-x-" + post["id"]}
        r = api("POST", self.base + "/api/v1/statuses", headers=headers, data=data)
        return {"id": str(r["id"]), "url": r.get("url")}


def run(x, bluesky, mastodon, state):
    if state.get("x_user_id"):
        x.uid = state["x_user_id"]
    uid = x.user_id()
    if state.get("x_user_id") != uid:
        state["x_user_id"] = uid
        save(state)
    incoming = x.new_posts(state.get("last_seen_id"))
    LOG.info("Fetched %d new X post(s) after checkpoint %s", len(incoming), state.get("last_seen_id") or "(none)")
    if not state.get("initialized"):
        state["initialized"] = True
        state["last_seen_id"] = incoming[-1]["id"] if incoming else "0"
        save(state)
        LOG.info("Initialized at %s without publishing previous posts", state["last_seen_id"])
        return
    for post in incoming:
        pid = post["id"]
        if int(pid) <= int(state.get("last_seen_id") or "0"):
            continue
        if post["repost"] or post.get("quote") or (post["reply_user"] and str(post["reply_user"]) != str(uid)):
            reason = "repost" if post["repost"] else "quote post" if post.get("quote") else "reply to another account"
            LOG.info("Skipping X post %s (%s)", pid, reason)
            state["last_seen_id"] = pid
            save(state)
            continue
        entry = state.setdefault("posts", {}).setdefault(pid, {})
        parent = state["posts"].get(post["parent"], {}) if post["parent"] else {}
        if "bluesky" not in entry:
            if fits_bluesky(post["text"]):
                entry["bluesky"] = bluesky.publish(post, parent.get("bluesky"))
                LOG.info("Published X post %s on Bluesky", pid)
            else:
                entry["bluesky"] = {"skipped": "exceeds Bluesky length limit"}
                LOG.info("Skipped X post %s on Bluesky (exceeds length limit); Mastodon remains eligible", pid)
            save(state)
        if "mastodon" not in entry:
            if fits_mastodon(post["text"]):
                entry["mastodon"] = mastodon.publish(post, parent.get("mastodon"))
                LOG.info("Published X post %s on Mastodon", pid)
            else:
                entry["mastodon"] = {"skipped": "exceeds Mastodon 500-character limit"}
                LOG.info("Skipped X post %s on Mastodon (over 500 characters)", pid)
            save(state)
        state["last_seen_id"] = pid
        save(state)
        LOG.info("Processed X post %s for crossposting", pid)
    if len(state["posts"]) > 2000:
        keep = sorted(state["posts"], key=int)[-2000:]
        state["posts"] = {p: state["posts"][p] for p in keep}
        save(state)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        x = X(required("X_USERNAME"), required("X_BEARER_TOKEN"),
              os.environ.get("X_USER_ID") or None)
        state = load()
        bsky = Bluesky(required("BLUESKY_HANDLE"), required("BLUESKY_APP_PASSWORD"))
        mast = Mastodon(required("MASTODON_BASE_URL"),
                        required("MASTODON_ACCESS_TOKEN"))
        run(x, bsky, mast, state)
        return 0
    except Exception as error:
        LOG.error("Crossposting stopped: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
