"""增长题库、按科目复习和分页回归；仅临时数据。"""
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_focus_workflow as fixtures
from app import db, math
from app.review_workload import daily_selection


class Workload(unittest.TestCase):
    setUp = fixtures.FocusWorkflow.setUp
    tearDown = fixtures.FocusWorkflow.tearDown

    def seed(self, count):
        with db.as_user(1), db.connect() as conn:
            conn.executemany("INSERT INTO math_problems(user_id,stem,answer,subject,planned_review,next_review) "
                             "VALUES(1,?,'答案',?,'2026-01-01','2026-01-01')",
                             [(f"临时测试题{i}", "信号与系统" if i == count-1 else "高等数学") for i in range(count)])

    def test_5000_due_items_are_counted_separately_and_paginated(self):
        self.seed(5000)
        with db.as_user(1):
            a = math.api_due(module="math")
            b = math.api_due(module="sig")
            self.assertEqual(a["daily"]["remaining"], 4999)
            self.assertEqual(b["daily"]["remaining"], 1)
            self.assertEqual(len(a["items"]), 20)
            self.assertEqual(a["next_offset"], 20)
            last = math.api_due(module="math", offset=4980)
            self.assertEqual(len(last["items"]), 19)
            self.assertIsNone(last["next_offset"])
            self.assertEqual(a["total"], 4999)
            self.assertTrue(all(p["subject"] != "信号与系统" for p in a["items"]+last["items"]))
            self.assertEqual(b["items"][0]["subject"], "信号与系统")

    def test_completion_never_uses_the_other_notebooks_progress(self):
        self.seed(30)
        with db.as_user(1):
            before_sig = math.api_due(module="sig")
            selected = math.api_due(module="math")["items"][0]["id"]
            math.api_result(selected, {"result": "right"}, module="math")
            after_math = math.api_due(module="math")
            self.assertEqual(after_math["daily"]["completed"], 1)
            self.assertEqual(after_math["daily"]["remaining"], 28)
            self.assertEqual(math.api_due(module="sig"), before_sig)

    def test_weekends_are_optional_in_both_notebooks(self):
        self.seed(20)
        with db.as_user(1):
            for module, pending in (("math", 19), ("sig", 1)):
                saturday = daily_selection(date(2026, 11, 14), date(2027, 12, 18), module)
                self.assertTrue(saturday["rest"])
                self.assertEqual(saturday["remaining"], 0)
                self.assertEqual(saturday["pending_total"], pending)
                monday = daily_selection(date(2026, 11, 16), date(2027, 12, 18), module)
                self.assertEqual(monday["remaining"], pending)

    def test_old_time_preferences_do_not_hide_due_questions(self):
        self.seed(40)
        with db.as_user(1):
            before = math.api_due(module="math")
            db.set_user_pref("review_minutes_min", "1")
            db.set_user_pref("review_item_minutes", "30")
            self.assertEqual(math.api_due(module="math"), before)
            self.assertEqual(before["daily"]["remaining"], 39)
            self.assertEqual(before["items"][0]["id"], 1)
        with db.as_user(2):
            self.assertEqual(math.api_due(module="math")["total"], 0)

    def test_all_bank_pages_keep_old_items_reachable(self):
        self.seed(505)
        with db.as_user(1):
            first = math.api_list(module="math", limit=100, offset=0)
            last = math.api_list(module="math", limit=100, offset=500)
            self.assertEqual(first["total"], 504)
            self.assertEqual(first["next_offset"], 100)
            self.assertEqual(len(first["items"]), 100)
            self.assertEqual(len(last["items"]), 4)
            self.assertIsNone(last["next_offset"])
            self.assertIn(1, [p["id"] for p in last["items"]])

    def test_calendar_summary_does_not_download_whole_question_bank(self):
        self.seed(5000)
        with db.as_user(1):
            day = (date.today()+timedelta(days=1)).isoformat()
            db.execute("UPDATE math_problems SET next_review=?,planned_review=? WHERE user_id=1", (day, day))
            plan = math.api_review_plan(module="math", days=14, summary=1)
            self.assertEqual(sum(d["count"] for d in plan["days"]), 4999)
            self.assertTrue(all(d["items"] == [] for d in plan["days"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
