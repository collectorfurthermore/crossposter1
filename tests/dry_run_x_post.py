#!/usr/bin/env python3
"""Read-only live smoke test for exactly one X post.

Usage: python tests/dry_run_x_post.py 2124658483307933859

Reads the single X post using the configured read-only X API bearer token,
reuses the production X text/link expansion, then attempts Open Graph
metadata retrieval for a Bluesky link card. No Bluesky login, Mastodon login,
posting, or state.json operations occur.
"""
import json
import os
import sys
from unittest.mock import patch
from urllib.parse import urlsplit

from crosspost import API, X, api, required
from link_preview import preview_for, external_article_url


def summarize(post):
    links = [link for link in post["links"] if external_article_url(link)]
    report = {
        "x_post_id": post["id"],
        "post_text": post["text"],
        "images": len(post["images"]),
        "article_links": links,
        "bluesky_embed_decision": "photo" if post["images"] else ("article_preview" if links else "text_only"),
    }
    if links and not post["images"]:
        card = preview_for(links[0])
        if card:
            domain = urlsplit(links[0]).hostname or ""
            report["preview"] = {
                "url": card["uri"],
                "title": card["title"],
                "description": card["description"],
                "thumbnail_present": bool(card["thumbnail"]),
                "thumbnail_bytes": len(card["thumbnail"] or b""),
                "result": ("fallback_only" if card["title"] == domain and not card["description"] and not card["thumbnail"] else "metadata_found"),
            }
        else:
            report["preview"] = {"result": "unsafe_or_unsupported_url"}
    return report


def main():
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        raise SystemExit("Usage: python tests/dry_run_x_post.py <numeric X post ID>")
    post_id = sys.argv[1]
    username = required("X_USERNAME")
    token = required("X_BEARER_TOKEN")
    x = X(username, token, os.environ.get("X_USER_ID") or None)
    uid = str(x.user_id())
    payload = api(
        "GET", f"{API}/tweets/{post_id}", headers=x.headers,
        params={
            "tweet.fields": "author_id,entities,attachments,referenced_tweets,in_reply_to_user_id,note_tweet",
            "expansions": "attachments.media_keys",
            "media.fields": "media_key,type,url,preview_image_url,alt_text",
        },
    )
    tweet = payload.get("data")
    if not isinstance(tweet, dict) or str(tweet.get("id")) != post_id:
        raise RuntimeError("X returned unexpected post data")
    if str(tweet.get("author_id")) != uid:
        raise RuntimeError("Refusing to inspect a post not owned by the configured X account")

    # Feed the exact response into the existing X extractor. The mock prevents
    # a user-timeline request and does not affect live X API calls outside this script.
    with patch("crosspost.api", return_value={
        "data": [tweet],
        "includes": payload.get("includes", {}),
        "meta": {},
    }):
        posts = x.new_posts(after="1")
    if len(posts) != 1:
        raise RuntimeError("Could not normalize the requested X post")

    print("READ-ONLY DRY RUN. No posts sent. No checkpoints modified.")
    print(json.dumps(summarize(posts[0]), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        # No credentials or API response payloads printed on failure.
        print(f"Dry run failed: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
