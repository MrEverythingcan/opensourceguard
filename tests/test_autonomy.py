import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path

from opensourceguard.autonomy import (
    AutonomyPolicy,
    AutonomousMaintainer,
    AutonomyStore,
    manual_issue_event,
    normalize_issue_event,
    verify_signature,
)


class AutonomyTests(unittest.TestCase):
    def test_signature_and_event_normalization(self):
        body = b'{"action":"opened"}'
        sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_signature("secret", body, sig))
        self.assertFalse(verify_signature("wrong", body, sig))
        event = normalize_issue_event({"action": "opened", "issue": {"number": 3, "title": "Bug", "labels": [], "user": {"login": "u"}}, "repository": {"full_name": "o/r"}}, delivery_id="d1")
        self.assertEqual(event["delivery_id"], "d1")
        self.assertEqual(event["repository"], "o/r")

    def test_queue_dedup_and_claim(self):
        with tempfile.TemporaryDirectory() as td:
            store = AutonomyStore(Path(td) / "jobs.sqlite3")
            event = {"delivery_id": "d1", "labels": [], "title": "Bug"}
            first, inserted = store.enqueue(event)
            second, inserted_again = store.enqueue(event)
            self.assertEqual(first, second)
            self.assertTrue(inserted)
            self.assertFalse(inserted_again)
            row = store.claim_next()
            self.assertEqual(row["status"], "running")
            store.finish(row["id"], "completed", {"verification_status": "verified"})
            self.assertEqual(store.counts()["completed"], 1)
            store.close()

    def test_manual_issue_event_fields_and_validation(self):
        event = manual_issue_event("  空输入崩溃  ", "详细描述", labels=["Bug", "bug", " UI "])
        self.assertEqual(event["title"], "空输入崩溃")
        self.assertEqual(event["labels"], ["bug", "ui"])
        self.assertEqual(event["event_name"], "manual")
        self.assertEqual(event["repository"], "local")
        self.assertTrue(event["delivery_id"].startswith("manual-"))
        csv_event = manual_issue_event("标题", labels="Bug, security")
        self.assertEqual(csv_event["labels"], ["bug", "security"])
        with self.assertRaises(ValueError):
            manual_issue_event("   ")

    def test_list_jobs_includes_event_summary(self):
        with tempfile.TemporaryDirectory() as td:
            store = AutonomyStore(Path(td) / "jobs.sqlite3")
            job_id, _ = store.enqueue(manual_issue_event("空输入崩溃", labels=["bug"]))
            store.enqueue({"delivery_id": "wh-1", "event_name": "issues", "title": "Webhook 问题", "labels": [], "repository": "o/r"})
            jobs = store.list_jobs()
            self.assertEqual(len(jobs), 2)
            manual = next(j for j in jobs if j["id"] == job_id)
            self.assertEqual(manual["title"], "空输入崩溃")
            self.assertEqual(manual["labels"], ["bug"])
            self.assertEqual(manual["source"], "手动录入")
            webhook = next(j for j in jobs if j["delivery_id"] == "wh-1")
            self.assertEqual(webhook["source"], "GitHub Webhook")
            self.assertEqual(webhook["repository"], "o/r")
            store.close()

    def test_risk_label_blocks_before_analyzer(self):
        with tempfile.TemporaryDirectory() as td:
            store = AutonomyStore(Path(td) / "jobs.sqlite3")
            called = []
            maintainer = AutonomousMaintainer(store, AutonomyPolicy(enabled=True), analyzer=lambda *_: called.append(1) or {"verification_status": "verified"})
            event = {"delivery_id": "d2", "labels": ["security"], "title": "security"}
            self.assertTrue(maintainer.accept(event)["accepted"])
            result = maintainer.run_next()
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(called, [])
            store.close()


if __name__ == "__main__":
    unittest.main()
