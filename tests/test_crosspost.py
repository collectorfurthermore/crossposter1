import unittest
from unittest.mock import patch

from crosspost import X, fits_bluesky, fits_mastodon


class CrosspostMediaTests(unittest.TestCase):
    def test_photo_link_is_removed_when_image_attached(self):
        post_id = "2120920808533242184"
        article = "https://thenewsground.com/serbs-for-trump-fec-reports-balkan-ties/"
        story = (
            "The FEC issued its 10th warning to the PAC behind Serbs for Trump "
            "since April 2024 over missing financial reports.\n\n"
            "The Nov. 3rd letter follows my investigation into the group:\n\n"
            "https://t.co/story https://t.co/photo"
        )
        response = {
            "data": [{
                "id": post_id,
                "author_id": "870429873621172226",
                "text": story,
                "attachments": {"media_keys": ["3_123"]},
                "entities": {
                    "urls": [
                        {"url": "https://t.co/story", "display_url": "thenewsground.com/...",
                         "expanded_url": article},
                        {"url": "https://t.co/photo", "display_url": "pic.x.com/abc123",
                         "expanded_url": f"https://x.com/ScottMStedman/status/{post_id}/photo/1"}
                    ]
                }
            }],
            "includes": {"media": [{"media_key": "3_123", "type": "photo",
                                   "url": "https://pbs.twimg.com/media/photo.jpg", "alt_text": "FEC notice"}]},
            "meta": {"result_count": 1},
        }
        with patch("crosspost.api", return_value=response):
            posts = X("ScottMStedman", "unused", uid="870429873621172226").new_posts("2120808940359524369")
        self.assertEqual(len(posts), 1)
        p = posts[0]
        self.assertIn(article, p["text"])
        self.assertNotIn("/photo/1", p["text"])
        self.assertNotIn("https://t.co/photo", p["text"])
        self.assertEqual(len(p["images"]), 1)
        self.assertTrue(fits_bluesky(p["text"]))

    def test_legacy_twitter_image_url_removed(self):
        result = {
            "data": [{
                "id": "2120920808533242184", "author_id": "870429873621172226",
                "text": "Post https://t.co/photo",
                "attachments": {"media_keys": ["3_123"]},
                "entities": {"urls": [{"url": "https://t.co/photo", "display_url": "pic.twitter.com/abc",
                                      "expanded_url": "https://twitter.com/author/status/2120920808533242184/photo/1"}]}
            }],
            "includes": {"media": [{"media_key": "3_123", "type": "photo", "url": "https://pbs.twimg.com/media/x.jpg"}]},
            "meta": {}
        }
        with patch("crosspost.api", return_value=result):
            p = X("ScottMStedman", "unused", uid="870429873621172226").new_posts("1")[0]
        self.assertEqual(p["text"], "Post")

    def test_bluesky_character_limit(self):
        self.assertTrue(fits_bluesky("a" * 300))
        self.assertFalse(fits_bluesky("a" * 301))
        self.assertTrue(fits_bluesky("👨‍👩‍👧‍👦" * 100))
        self.assertFalse(fits_bluesky("👨‍👩‍👧‍👦" * 300))

    def test_mastodon_character_limit(self):
        self.assertTrue(fits_mastodon("x" * 500))
        self.assertFalse(fits_mastodon("x" * 501))


if __name__ == "__main__":
    unittest.main()
