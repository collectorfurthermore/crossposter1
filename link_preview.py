"""Safe, best-effort Open Graph previews for links on Bluesky posts.

Only used for posts without attached photos. Never publishes or authenticates.
"""

import ipaddress
import json
import re
import socket
from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import quote, urljoin, urlsplit

import requests
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_HTML_BYTES = 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_THUMB_BYTES = 950_000
HEADERS = {
    "User-Agent": "NewsgroundCrossposter/1.0 (+link-preview)",
    "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,image/jpeg,image/png,*/*;q=0.5",
}


def public_web_url(url):
    """Reject local/internal destinations, nonstandard ports, and non-public DNS results."""
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").strip(".").lower()
        if parsed.scheme not in ("https", "http") or not hostname:
            return False
        if parsed.username or parsed.password or parsed.port not in (None, 80, 443):
            return False
        if not re.fullmatch(r"[a-z0-9._-]+", hostname) or "." not in hostname:
            return False
        if hostname.endswith((".localhost", ".local", ".internal", ".test", ".example")):
            return False
        try:
            ipaddress.ip_address(hostname)
            return False  # Never fetch a literal IP address.
        except ValueError:
            pass
        addresses = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(item[4][0]).is_global for item in addresses)
    except (ValueError, TypeError, OSError):
        return False


def external_article_url(url):
    """Only external article links, not X/Twitter, t.co, or media attachments."""
    hostname = (urlsplit(url).hostname or "").lower()
    if hostname in ("x.com", "www.x.com", "twitter.com", "www.twitter.com", "t.co", "pic.x.com"):
        return False
    return public_web_url(url)


def fetch_limited(url, max_bytes, expected):
    """Manually validate each redirect, cap streaming response size, and close streams."""
    for _ in range(4):
        if not public_web_url(url):
            raise ValueError("Unsafe preview destination")
        response = requests.get(url, headers=HEADERS, stream=True, timeout=(5, 10), allow_redirects=False)
        try:
            if 300 <= response.status_code < 400 and response.headers.get("Location"):
                url = urljoin(url, response.headers["Location"])
                continue
            response.raise_for_status()
            mime = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if expected == "html" and mime and mime not in ("text/html", "application/xhtml+xml"):
                raise ValueError("Page is not HTML")
            if expected == "image" and mime and mime not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
                raise ValueError("Preview is not a supported image")
            if expected == "json" and mime and mime not in ("application/json", "application/problem+json"):
                raise ValueError("Preview is not JSON")
            body = bytearray()
            for chunk in response.iter_content(65536):
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise ValueError("Preview response too large")
            return bytes(body), url
        finally:
            response.close()
    raise ValueError("Too many preview redirects")


class MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values = {}
        self.title_parts = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            values = dict((key.lower(), value) for key, value in attrs if key and value is not None)
            name = (values.get("property") or values.get("name") or "").lower()
            content = values.get("content", "").strip()
            if name and content:
                self.values.setdefault(name, content)

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)

    def first(self, *keys):
        return next((self.values[key] for key in keys if self.values.get(key)), "")


def thumbnail_jpeg(image_data):
    """Produce a Bluesky-compatible thumbnail under the ATProto 1 MB limit."""
    with Image.open(BytesIO(image_data)) as raw:
        if raw.width * raw.height > 25_000_000:
            raise ValueError("Preview image too many pixels")
        image = ImageOps.exif_transpose(raw)
        if image.mode != "RGB":
            rgba = image.convert("RGBA")
            background = Image.new("RGBA", image.size, (255, 255, 255, 255))
            background.alpha_composite(rgba)
            image = background.convert("RGB")
        for size in (1200, 900, 600, 400):
            scaled = image.copy()
            scaled.thumbnail((size, size))
            for quality in (85, 70, 55, 40):
                buffer = BytesIO()
                scaled.save(buffer, format="JPEG", quality=quality, optimize=True)
                if buffer.tell() <= MAX_THUMB_BYTES:
                    return buffer.getvalue()
    raise ValueError("Preview thumbnail cannot fit")



def cardyb_metadata(url):
    """Ask the official Bluesky link-preview helper for public URL metadata.

    This is only a fallback when the source page does not provide enough
    metadata to this runner. It never uses Bluesky credentials.
    """
    service_url = "https://cardyb.bsky.app/v1/extract?url=" + quote(url, safe="")
    body, _ = fetch_limited(service_url, MAX_HTML_BYTES, "json")
    payload = json.loads(body.decode("utf-8", errors="replace"))
    if not isinstance(payload, dict):
        raise ValueError("CardyB returned an invalid response")
    if payload.get("error"):
        raise ValueError("CardyB reported a retrieval error")
    return {
        "title": str(payload.get("title") or "").strip(),
        "description": str(payload.get("description") or "").strip(),
        "image": str(payload.get("image") or "").strip(),
    }


def preview_thumbnail(url):
    if not public_web_url(url):
        raise ValueError("Unsafe preview thumbnail URL")
    image_data, _ = fetch_limited(url, MAX_IMAGE_BYTES, "image")
    return thumbnail_jpeg(image_data)


def preview_for(url):
    """Build a card using page metadata, then Bluesky's own extractor if needed.

    Always use the original article URL as the card destination.
    Report the metadata source and failures for dry-run diagnostics.
    """
    if not external_article_url(url):
        return None
    hostname = urlsplit(url).hostname or url
    title = hostname
    description = ""
    thumbnail = None
    source = "fallback"
    diagnostics = []

    try:
        body, final_url = fetch_limited(url, MAX_HTML_BYTES, "html")
        parser = MetadataParser()
        parser.feed(body.decode("utf-8", errors="replace"))
        page_title = parser.first("og:title", "twitter:title") or " ".join(parser.title_parts).strip()
        if page_title:
            title = page_title
            source = "site"
        description = parser.first("og:description", "twitter:description", "description")
        image_url = parser.first("og:image:secure_url", "og:image", "twitter:image")
        if image_url:
            try:
                thumbnail = preview_thumbnail(urljoin(final_url, image_url))
            except (requests.RequestException, OSError, ValueError, UnidentifiedImageError) as error:
                diagnostics.append("site_image:" + type(error).__name__)
    except (requests.RequestException, OSError, ValueError) as error:
        diagnostics.append("site_html:" + type(error).__name__)
        if isinstance(error, requests.HTTPError) and error.response is not None:
            diagnostics.append("site_status:" + str(error.response.status_code))

    if title == hostname or not description or thumbnail is None:
        try:
            cardyb = cardyb_metadata(url)
            improved = False
            if title == hostname and cardyb["title"] and cardyb["title"] != hostname:
                title = cardyb["title"]
                improved = True
            if not description and cardyb["description"]:
                description = cardyb["description"]
                improved = True
            if thumbnail is None and cardyb["image"]:
                try:
                    thumbnail = preview_thumbnail(cardyb["image"])
                    improved = True
                except (requests.RequestException, OSError, ValueError, UnidentifiedImageError) as error:
                    diagnostics.append("cardyb_image:" + type(error).__name__)
            if improved:
                source = "cardyb" if source == "fallback" else "site+cardyb"
        except (requests.RequestException, OSError, ValueError, json.JSONDecodeError) as error:
            diagnostics.append("cardyb:" + type(error).__name__)

    return {
        "uri": url, "title": title[:300], "description": description[:1000],
        "thumbnail": thumbnail, "source": source, "diagnostics": diagnostics,
    }
