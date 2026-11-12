"""错题录入、批次顺延、英语暂停回归；只使用临时数据库。"""
import gc
import os
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db, jobs, math, scheduler
from app.config import settings
from app.review_plan import arrange


class FocusWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="prism_focus_")
        self.old_path = settings.db_path
        settings.db_path = Path(self.tmp.name) / "app.db"
        db._initialized_user_dbs.clear()
        db.init_db()
        with db.as_system():
            for uid in (1, 2):
                db.execute("INSERT INTO users(id,username,display_name,password_hash,salt,active,approved) VALUES(?,?,?,?,?,1,1)",
                           (uid, f"tester{uid}", "测试", "x", "x"))

    def tearDown(self):
        jobs._jobs.clear()
        settings.db_path = self.old_path
        db._initialized_user_dbs.clear()
        gc.collect()
        self.tmp.cleanup()

    def batch(self, original, subject="高等数学"):
        uid = db.require_user()
        return db.execute("INSERT INTO math_problems(user_id,subject,stem,planned_review,next_review) VALUES(?,?,?,?,?)",
                          (uid, subject, "测试题", original, original))

    def test_batches_skip_weekend_without_colliding(self):
        with db.as_user(1):
            for day in ("2026-11-14", "2026-11-15", "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19"):
                self.batch(day)
            self.batch("2026-11-14")  # 同日多题是同一批。
            with db.connect() as conn:
                arrange(conn, 1)
            rows = db.query("SELECT planned_review,next_review FROM math_problems ORDER BY planned_review,id")
            self.assertEqual([r["next_review"] for r in rows],
                             ["2026-11-16", "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20", "2026-11-23"])
            with db.connect() as conn:
                arrange(conn, 1)
            self.assertEqual(rows, db.query("SELECT planned_review,next_review FROM math_problems ORDER BY planned_review,id"))
            db.execute("DELETE FROM math_problems WHERE planned_review='2026-11-14'")
            with db.connect() as conn:
                arrange(conn, 1)
            self.assertEqual(db.query_one("SELECT next_review FROM math_problems ORDER BY planned_review LIMIT 1")["next_review"], "2026-11-17")

    def test_subject_and_user_are_isolated(self):
        with db.as_user(1):
            self.batch("2026-11-14")
            self.batch("2026-11-15")
            sig = self.batch("2026-11-16", "信号与系统")
            with db.connect() as conn:
                arrange(conn, 1)
            self.assertEqual(math._get(sig)["next_review"], "2026-11-16")
        with db.as_user(2):
            own = self.batch("2026-11-16")
            with db.connect() as conn:
                arrange(conn, 2)
            self.assertEqual(math._get(own)["next_review"], "2026-11-16")
            self.assertEqual(len(math.api_review_plan(module="sig", days=14)["days"][0]["items"]), 0)

    def test_weekend_optional_practice_replaces_old_due_date(self):
        class Saturday(date):
            @classmethod
            def today(cls):
                return cls(2026, 11, 14)
        with db.as_user(1), patch.object(math, "date", Saturday), patch.object(db, "date", Saturday):
            pid = self.batch("2026-11-16")
            self.assertEqual(math.api_due(module="math")["items"], [])
            self.assertTrue(math.api_due(module="math")["rest"])
            self.assertIsNotNone(math.random_problem(module="math"))
            result = math.apply_result(pid, "right")
            self.assertEqual(result["next_review"], "2026-11-17")
            self.assertEqual(result["planned_review"], "2026-11-17")
            self.assertEqual(result["review_count"], 1)

    def test_save_retry_is_idempotent_and_scoped(self):
        with db.as_user(1):
            a = math.create_problem("题干", subject="高等数学", request_id="request-1")
            b = math.create_problem("题干", subject="高等数学", request_id="request-1")
            self.assertEqual(a["id"], b["id"])
            self.assertEqual(math.stats("math")["total"], 1)
        with db.as_user(2):
            math.create_problem("另一个账号的题", subject="高等数学", request_id="request-1")
            self.assertEqual(math.stats("math")["total"], 1)

    def test_extract_does_not_require_answer_and_propagates_uncertainty(self):
        with patch.object(math, "get_provider") as provider:
            provider.return_value.chat_vision.return_value = '{"stem":"8.6 测试题", "answer":"", "uncertain":["题号被遮挡"]}'
            result = math.extract_from_image("data:image/png;base64,AA==")
            self.assertEqual(result["answer"], "")
            self.assertEqual(result["uncertain"], ["题号被遮挡"])
            self.assertIn("不要解题", provider.return_value.chat_vision.call_args.args[0])

    def test_background_jobs_are_scoped_and_retry_safe(self):
        calls = []
        with db.as_user(1):
            a = jobs.start("math-extract-123", lambda: calls.append(1) or {"stem":"题干"}, ephemeral=True)
            for _ in range(100):
                if not jobs.status("math-extract-123")["running"]:
                    break
                time.sleep(.01)
            b = jobs.start("math-extract-123", lambda: calls.append(2), ephemeral=True)
            self.assertTrue(b["ok"])
            self.assertEqual(calls, [1])
        with db.as_user(2):
            self.assertFalse(jobs.status("math-extract-123")["exists"])

    def test_english_is_paused_even_with_ai_key(self):
        with patch.object(settings, "english_enabled", False), patch.object(scheduler, "generate_daily") as generate:
            self.assertIsNone(scheduler.start_scheduler())
            scheduler._try_generate_today()
            generate.assert_not_called()

    def test_migration_preserves_original_and_is_idempotent(self):
        with db.as_user(1):
            self.batch("2026-11-14")
            self.batch("2026-11-15")
            self.batch("2026-11-16")
            with db.connect() as conn:
                conn.execute("ALTER TABLE math_problems DROP COLUMN planned_review")
                db._migrate(conn)
                db._migrate(conn)
            rows = db.query("SELECT planned_review,next_review FROM math_problems ORDER BY id")
            self.assertEqual([r["next_review"] for r in rows], ["2026-11-16", "2026-11-17", "2026-11-18"])
            self.assertEqual([r["planned_review"] for r in rows], ["2026-11-14", "2026-11-15", "2026-11-16"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
