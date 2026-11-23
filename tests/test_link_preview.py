import io
import socket
import unittest
from unittest.mock import patch

from PIL import Image

from link_preview import (MetadataParser, external_article_url, fetch_limited,
                          preview_for, public_web_url, thumbnail_jpeg)


class FakeResponse:
    def __init__(self, data=b"", status=200, headers=None):
        self.data = data
        self.status_code = status
        self.headers = headers or {"Content-Type": "text/html"}
        self.closed = False

    def iter_content(self, chunk_size):
        for index in range(0, len(self.data), chunk_size):
            yield self.data[index:index + chunk_size]

    def raise_for_status(self):
        if self.status_code >= 400:
            from requests import HTTPError
            raise HTTPError("blocked")

    def close(self):
        self.closed = True


def public_dns(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443))]


class PreviewTests(unittest.TestCase):
    @patch("link_preview.socket.getaddrinfo", side_effect=public_dns)
    def test_article_url_is_public_and_x_post_is_not(self, _dns):
        self.assertTrue(external_article_url("https://www.nytimes.com/2026/11/22/us/example.html"))
        self.assertFalse(external_article_url("https://x.com/user/status/123"))
        self.assertFalse(external_article_url("https://t.co/abc"))
        self.assertFalse(public_web_url("http://127.0.0.1/admin"))
        self.assertFalse(public_web_url("http://metadata.google.internal/latest"))
        self.assertFalse(public_web_url("http://example.org:8080/dashboard"))

    @patch("link_preview.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.5", 443))])
    def test_private_dns_is_rejected(self, _dns):
        self.assertFalse(public_web_url("https://internal.public-looking.com/path"))

    @patch("link_preview.socket.getaddrinfo", side_effect=public_dns)
    @patch("link_preview.requests.get")
    def test_metadata_and_thumbnail(self, get, _dns):
        page = b'''<html><head><meta property="og:title" content="Alice Huang &amp; Intelligence">
          <meta property="og:description" content="Reporting on an MSS contact">
          <meta property="og:image" content="/cover.jpg"><title>Fallback headline</title></head></html>'''
        image = Image.new("RGB", (600, 315), "blue")
        image_bytes = io.BytesIO()
        image.save(image_bytes, format="PNG")
        get.side_effect = [FakeResponse(page), FakeResponse(image_bytes.getvalue(), headers={"Content-Type": "image/png"})]
        card = preview_for("https://www.nytimes.com/2026/11/22/us/example.html")
        self.assertEqual(card["title"], "Alice Huang & Intelligence")
        self.assertEqual(card["description"], "Reporting on an MSS contact")
        self.assertEqual(card["uri"], "https://www.nytimes.com/2026/11/22/us/example.html")
        self.assertTrue(card["thumbnail"].startswith(b"\xff\xd8"))
        self.assertLess(len(card["thumbnail"]), 1_000_000)
        self.assertEqual(get.call_args_list[1].args[0], "https://www.nytimes.com/cover.jpg")

    @patch("link_preview.socket.getaddrinfo", side_effect=public_dns)
    @patch("link_preview.requests.get")
    def test_blocked_page_falls_back_to_domain(self, get, _dns):
        get.return_value = FakeResponse(status=403)
        card = preview_for("https://www.nytimes.com/2026/11/22/us/example.html")
        self.assertEqual(card["title"], "www.nytimes.com")
        self.assertIsNone(card["thumbnail"])

    @patch("link_preview.socket.getaddrinfo", side_effect=public_dns)
    @patch("link_preview.requests.get")
    def test_redirect_to_private_url_is_blocked(self, get, _dns):
        response = FakeResponse(status=302, headers={"Location": "http://127.0.0.1/latest"})
        get.return_value = response
        with self.assertRaises(ValueError):
            fetch_limited("https://example.org/story", 1000, "html")
        self.assertTrue(response.closed)
        self.assertEqual(get.call_count, 1)

    @patch("link_preview.socket.getaddrinfo", side_effect=public_dns)
    @patch("link_preview.requests.get")
    def test_oversize_html_is_blocked(self, get, _dns):
        response = FakeResponse(b"A" * 1024)
        get.return_value = response
        with self.assertRaisesRegex(ValueError, "too large"):
            fetch_limited("https://example.org/story", 100, "html")
        self.assertTrue(response.closed)

    def test_html_title_fallback(self):
        parser = MetadataParser()
        parser.feed("<head><title>Article title &amp; subtitle</title></head>")
        self.assertEqual(" ".join(parser.title_parts), "Article title & subtitle")

    def test_png_converts_to_jpeg(self):
        image = Image.new("RGBA", (100, 50), (80, 70, 60, 125))
        data = io.BytesIO()
        image.save(data, "PNG")
        thumb = thumbnail_jpeg(data.getvalue())
        self.assertTrue(thumb.startswith(b"\xff\xd8"))
        self.assertLess(len(thumb), 1_000_000)


if __name__ == "__main__":
    unittest.main()
