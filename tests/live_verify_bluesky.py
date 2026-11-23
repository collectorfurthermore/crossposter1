#!/usr/bin/env python3
"""Deliberately publish ONE selected existing X post to Bluesky for visual verification.

The workflow must require an explicit human-entered confirmation before calling this.
No Mastodon calls, X timeline scan, or checkpoint read/write.
"""
import os
import sys
from urllib.parse import quote

from crosspost import Bluesky, fits_bluesky
from dry_run_x_post import fetch_selected_post
from link_preview import preview_for, external_article_url


POST_ID = "2124658483307933859"
CONFIRM = "PUBLISH_ONE_BLUESKY_TEST"


def main():
    if os.environ.get("CONFIRM_SINGLE_POST") != CONFIRM:
        raise RuntimeError("Confirmation phrase missing; refusing to publish")
    post = fetch_selected_post(POST_ID)
    if post["id"] != POST_ID:
        raise RuntimeError("Unexpected X post ID")
    if post["images"]:
        raise RuntimeError("Test requires a link-only X post")
    urls = [url for url in post.get("links", []) if external_article_url(url)]
    if not urls:
        raise RuntimeError("Selected X post has no external article URL")
    if not fits_bluesky(post["text"]):
        raise RuntimeError("X post exceeds Bluesky limits")
    preview = preview_for(urls[0])
    if not preview or not preview["thumbnail"] or not preview["description"] or preview["title"] == "www.nytimes.com":
        raise RuntimeError("Rich preview unavailable; refusing to publish a domain-only card")

    print(f"Preview confirmed: {preview['title']}")
    print(f"Preview source: {preview['source']}")
    print(f"Image size: {len(preview['thumbnail'])} bytes")
    print("Publishing exactly one post to the configured Bluesky account...")
    publisher = Bluesky(os.environ["BLUESKY_HANDLE"], os.environ["BLUESKY_APP_PASSWORD"])
    sent = publisher.publish(post)
    rkey = sent["uri"].rsplit("/", 1)[-1]
    handle = os.environ["BLUESKY_HANDLE"].removeprefix("@")
    print("BLUESKY_TEST_POST_URL=https://bsky.app/profile/" + quote(handle, safe=".-") + "/post/" + rkey)
    print("No Mastodon calls or state changes were made.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"Test refused/failed: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
