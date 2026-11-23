"""Run a read-only link-preview smoke test using real article URLs.

This script never imports a publisher, logs in, or submits a post.
"""
import json

from link_preview import preview_for


URLS = [
    "https://www.nytimes.com/",
    "https://thenewsground.com/",
]

for url in URLS:
    result = preview_for(url)
    print(json.dumps({
        "url": url,
        "safe": bool(result),
        "title": (result or {}).get("title"),
        "description_present": bool((result or {}).get("description")),
        "thumbnail_present": bool((result or {}).get("thumbnail")),
        "thumbnail_bytes": len((result or {}).get("thumbnail") or b""),
    }))
