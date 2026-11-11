import copy
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crosspost import Processor, XClient, expand_x_urls, trim_for_platform, image_bytes


class FakeX:
    user_id = None

    def __init__(self, posts, account_id="12"):
        self.posts = posts
        self.account_id = account_id
        self.history = []

    def resolve_user(self):
        self.user_id = self.account_id
        return self.account_id

    def fetch(self, since_id=None):
        self.history.append(since_id)
        return [copy.deepcopy(p) for p in self.posts if not since_id or int(p["id"]) > int(since_id)]


class FakePublisher:
    def __init__(self, name):
        self.name = name
        self.calls = []
        self.fail = False

    def publish(self, post, parent):
        if self.fail:
            raise RuntimeError(f"{self.name} unavailable")
        self.calls.append((post["id"], parent))
        if self.name == "bluesky":
            result = {"uri": f"at://example/{post['id']}", "cid": f"cid-{post['id']}"}
            result["root"] = parent["root"] if parent else {"uri": result["uri"], "cid": result["cid"]}
            return result
        return {"id": f"masto-{post['id']}"}


def sample(pid, parent=None, other_reply=None, is_repost=False):
    return {
        "id": str(pid), "text": f"New post {pid}", "images": [],
        "parent_id": str(parent) if parent else None,
        "reply_to_user_id": other_reply,
        "is_repost": is_repost,
    }


class CrosspostTests(unittest.TestCase):
    def setUp(self):
        self.saved = []
        self.saver = lambda state: self.saved.append(copy.deepcopy(state))

    def test_first_run_primes_without_backfill(self):
        state = {"initialized": False, "last_seen_id": None, "posts": {}, "x_user_id": None}
        x = FakeX([sample(9), sample(10)])
        b, m = FakePublisher("bluesky"), FakePublisher("mastodon")
        Processor(x, b, m, state, self.saver).run()
        self.assertEqual("10", state["last_seen_id"])
        self.assertEqual("12", state["x_user_id"])
        self.assertFalse(b.calls)
        self.assertFalse(m.calls)

    def test_new_posts_publish_in_order_and_are_not_repeated(self):
        state = {"initialized": True, "last_seen_id": "10", "posts": {}, "x_user_id": None}
        x = FakeX([sample(11), sample(12)])
        b, m = FakePublisher("bluesky"), FakePublisher("mastodon")
        processor = Processor(x, b, m, state, self.saver)
        self.assertEqual(2, processor.run())
        self.assertEqual(0, processor.run())
        self.assertEqual(["11", "12"], [p for p, _ in b.calls])
        self.assertEqual(["11", "12"], [p for p, _ in m.calls])
        self.assertEqual("12", state["last_seen_id"])
        self.assertEqual("12", x.user_id)

    def test_partial_success_does_not_duplicate_bluesky_on_retry(self):
        state = {"initialized": True, "last_seen_id": "10", "posts": {}, "x_user_id": "12"}
        x = FakeX([sample(11)])
        b, m = FakePublisher("bluesky"), FakePublisher("mastodon")
        m.fail = True
        with self.assertRaises(RuntimeError):
            Processor(x, b, m, state, self.saver).run()
        self.assertEqual("10", state["last_seen_id"])
        self.assertIn("bluesky", self.saved[-1]["posts"]["11"])
        m.fail = False
        Processor(x, b, m, state, self.saver).run()
        self.assertEqual(1, len(b.calls))
        self.assertEqual(1, len(m.calls))
        self.assertEqual("11", state["last_seen_id"])

    def test_threads_remain_threads_and_replies_to_other_users_skipped(self):
        state = {"initialized": True, "last_seen_id": "1", "posts": {}, "x_user_id": "12"}
        x = FakeX([sample(2), sample(3, parent=2, other_reply="12"), sample(4, parent=700, other_reply="99")])
        b, m = FakePublisher("bluesky"), FakePublisher("mastodon")
        Processor(x, b, m, state, self.saver).run()
        self.assertEqual(["2", "3"], [p for p, _ in b.calls])
        self.assertEqual(["2", "3"], [p for p, _ in m.calls])
        self.assertEqual("at://example/2", b.calls[1][1]["uri"])
        self.assertEqual("masto-2", m.calls[1][1]["id"])
        self.assertEqual("4", state["last_seen_id"])

    def test_expand_links_and_remove_photo_link(self):
        entities = {"urls": [
            {"url": "https://t.co/aaa", "expanded_url": "https://thenewsground.com/story"},
            {"url": "https://t.co/bbb", "expanded_url": "https://x.com/user/status/100/photo/1", "display_url": "pic.twitter.com/bbb"},
        ]}
        self.assertEqual("New story https://thenewsground.com/story", expand_x_urls("New story https://t.co/aaa https://t.co/bbb", entities, True))

    def test_long_posts_are_shortened_with_citation(self):
        long = "🙂" * 400
        short = trim_for_platform(long, "123", 300, 3000)
        self.assertLessEqual(len(short), 300)
        self.assertIn("https://x.com/i/status/123", short)
        self.assertEqual("Hello", trim_for_platform("Hello", "123", 300))

    def test_x_timeline_long_text_and_image(self):
        payload = {"data": [{
            "id": "77", "author_id": "12", "text": "truncated",
            "note_tweet": {"text": "Full long post https://t.co/abc", "entities": {"urls": [{"url": "https://t.co/abc", "expanded_url": "https://thenewsground.com"}]}},
            "attachments": {"media_keys": ["3_1"]},
        }], "includes": {"media": [{"media_key": "3_1", "type": "photo", "url": "https://pbs.twimg.com/media/abc.jpg", "alt_text": "Newspaper"}]}, "meta": {"result_count": 1}}
        with patch("crosspost.request_json", return_value=payload) as mock_fetch:
            posts = XClient("secret", "myname", "12").fetch("76")
        self.assertEqual("Full long post https://thenewsground.com", posts[0]["text"])
        self.assertEqual("Newspaper", posts[0]["images"][0]["alt"])
        self.assertEqual("76", mock_fetch.call_args.kwargs["params"]["since_id"])

    def test_initialization_fetches_only_one_small_page(self):
        payload = {
            "data": [{"id": "100", "author_id": "12", "text": "Newest post"}],
            "meta": {"next_token": "older-posts-we-must-not-fetch"},
        }
        with patch("crosspost.request_json", return_value=payload) as mock_fetch:
            posts = XClient("secret", "myname", "12").fetch()
        self.assertEqual(["100"], [p["id"] for p in posts])
        self.assertEqual(1, mock_fetch.call_count)
        self.assertEqual(5, mock_fetch.call_args.kwargs["params"]["max_results"])

    def test_image_downloader_refuses_unknown_hosts(self):
        with self.assertRaises(RuntimeError):
            image_bytes("https://not-x.example/test.png")


if __name__ == "__main__":
    unittest.main()
