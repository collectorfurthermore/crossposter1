"""Read-only live preview smoke check, including the specified NYT article."""
import json
from urllib.parse import urlsplit
from link_preview import preview_for

URLS = [
    "https://www.nytimes.com/2026/11/22/us/politics/alice-huang-china-spy-agency-nvidia.html",
    "https://thenewsground.com/",
]
success = True
for url in URLS:
    result = preview_for(url)
    host = urlsplit(url).hostname
    rich = bool(result and result["title"] != host and result["description"])
    print(json.dumps({
        "url": url,
        "safe": bool(result),
        "title": (result or {}).get("title"),
        "description_present": bool((result or {}).get("description")),
        "thumbnail_present": bool((result or {}).get("thumbnail")),
        "thumbnail_bytes": len((result or {}).get("thumbnail") or b""),
        "source": (result or {}).get("source"),
        "diagnostics": (result or {}).get("diagnostics"),
        "rich_metadata": rich,
    }))
    if "nytimes.com" in url and not rich:
        success = False

if not success:
    raise SystemExit("NYT rich preview still unavailable; do not merge")
