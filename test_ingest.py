import io
import json
import unittest
from contextlib import redirect_stderr
from unittest.mock import call, patch

import requests

import ingest


def response(status=200, cards=None, total=1):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps({
        "data": cards or [{"id": "sv8pt5-1"}],
        "pageSize": 1,
        "totalCount": total,
    }).encode()
    result._content_consumed = True
    return result


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.get = self.enterContext(patch("ingest.requests.get"))
        self.sleep = self.enterContext(patch("ingest.time.sleep"))
        self.errors = self.enterContext(redirect_stderr(io.StringIO()))

    def test_transient_http_errors_recover(self):
        for status in (429, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.get.reset_mock()
                self.get.side_effect = [response(status), response()]
                self.assertEqual(ingest.fetch_set_cards("sv8pt5"), [{"id": "sv8pt5-1"}])
                self.assertEqual(self.get.call_count, 2)

    def test_network_errors_recover_with_backoff(self):
        self.get.side_effect = [requests.Timeout(), requests.ConnectionError(), response()]
        ingest.fetch_set_cards("sv8pt5")
        self.assertEqual(self.sleep.call_args_list, [call(5), call(10)])

    def test_non_retryable_errors_fail_immediately(self):
        for status in (400, 401, 403, 404):
            with self.subTest(status=status):
                self.get.reset_mock()
                self.get.side_effect = [response(status)]
                with self.assertRaises(requests.HTTPError):
                    ingest.fetch_set_cards("sv8pt5")
                self.assertEqual(self.get.call_count, 1)
        self.sleep.assert_not_called()

    def test_retries_only_failed_page(self):
        self.get.side_effect = [
            response(total=2), response(500),
            response(cards=[{"id": "sv8pt5-2"}], total=2),
        ]
        cards = ingest.fetch_set_cards("sv8pt5", "test-key")
        self.assertEqual([c["id"] for c in cards], ["sv8pt5-1", "sv8pt5-2"])
        self.assertEqual([c.kwargs["params"]["page"] for c in self.get.call_args_list], [1, 2, 2])
        self.assertEqual(self.get.call_args.kwargs["headers"], {"X-Api-Key": "test-key"})

    def test_exhaustion_does_not_write_partial_data(self):
        self.get.side_effect = [response(total=2)] + [response(500) for _ in range(5)]
        with patch("sys.argv", ["ingest.py"]), patch("ingest.archive_raw") as archive, patch("ingest.load") as load:
            self.assertEqual(ingest.main(), 1)
            archive.assert_not_called()
            load.assert_not_called()
        self.assertEqual(self.get.call_count, 6)
        self.assertEqual(self.sleep.call_args_list, [call(1), call(5), call(10), call(20), call(40)])
        self.assertIn("No archive or database updates", self.errors.getvalue())

    def test_persistent_timeout_is_bounded(self):
        self.get.side_effect = requests.Timeout()
        with self.assertRaises(requests.Timeout):
            ingest.fetch_set_cards("sv8pt5")
        self.assertEqual(self.get.call_count, 5)


if __name__ == "__main__":
    unittest.main()
