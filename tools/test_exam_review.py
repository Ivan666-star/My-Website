"""考研目标日期、阶段收紧和考前批次边界测试；不触碰正式数据。"""
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_focus_workflow as fixtures
from app import db, math, exam_review
from app.review_plan import arrange


class ExamReview(unittest.TestCase):
    setUp = fixtures.FocusWorkflow.setUp
    tearDown = fixtures.FocusWorkflow.tearDown

    def test_phase_boundaries(self):
        exam = date(2027, 12, 18)
        for days, cap in ((401,60),(121,60),(120,30),(61,30),(60,14),(31,14),(30,7),(8,7),(7,3),(1,3),(0,0),(-1,0)):
            self.assertEqual(exam_review.phase(exam-timedelta(days=days), exam)["max_interval"], cap)

    def test_long_gap_stops_at_next_phase(self):
        exam = date(2027, 12, 18)
        today = exam-timedelta(days=130)
        self.assertEqual(exam_review.target(today,60,exam), (exam-timedelta(days=120)).isoformat())
        self.assertEqual(exam_review.target(today,1,exam), (today+timedelta(days=1)).isoformat())
        self.assertEqual(exam_review.target(exam,1,exam), "")

    def test_entering_sprint_updates_mature_problems_only_once(self):
        class Today(date):
            @classmethod
            def today(cls):
                return date(2027,12,8)
        with db.as_user(1), patch.object(math,"date",Today):
            p = math.create_problem("已掌握但需考前回顾",subject="高等数学",answer="答案")
            db.execute("UPDATE math_problems SET review_count=6,last_review_at='2027-12-05',planned_review='2028-01-10',next_review='2028-01-10' WHERE id=?",(p["id"],))
            policy=math.refresh_exam_review()
            after=math._get(p["id"])
            self.assertEqual(policy["max_interval"],7)
            self.assertLessEqual(after["next_review"],"2027-12-15")
            self.assertEqual(after["review_count"],6)
            math.refresh_exam_review()
            self.assertEqual(after,math._get(p["id"]))

    def test_exam_date_is_user_scoped_and_time_budget_is_inactive(self):
        with db.as_user(1):
            db.set_user_pref("exam_date","2027-11-20")
            db.set_user_pref("review_minutes_min","45")
            db.set_user_pref("review_minutes_max","60")
            policy=math.refresh_exam_review()
            self.assertEqual(policy["exam_date"],"2027-11-20")
            self.assertNotIn("daily_minutes_min", policy)
            self.assertNotIn("daily_minutes_max", policy)
        with db.as_user(2):
            self.assertEqual(math.refresh_exam_review()["exam_date"],"2027-12-18")

    def test_repeated_mistakes_are_prioritized(self):
        with db.as_user(1):
            a=math.create_problem("近期反复做错的旧题",subject="高等数学")
            b=math.create_problem("一般到期的新题",subject="高等数学")
            math.refresh_exam_review()
            db.execute("UPDATE math_problems SET planned_review='2026-01-01',next_review='2026-01-01' WHERE user_id=1")
            db.execute("INSERT INTO math_review_log(user_id,problem_id,result,created_at) VALUES(1,?,'wrong',?)",
                       (a["id"],(date.today()-timedelta(days=1)).isoformat()))
            self.assertEqual(math.api_due(module="math",limit=1)["items"][0]["id"],a["id"])

    def test_batches_are_kept_separate_before_exam(self):
        with db.as_user(1):
            a=math.create_problem("考前批次一",subject="高等数学")
            b=math.create_problem("考前批次二",subject="高等数学")
            with db.connect() as conn:
                conn.execute("UPDATE math_problems SET planned_review='2027-12-17',next_review='2027-12-17' WHERE id=?",(a["id"],))
                conn.execute("UPDATE math_problems SET planned_review='2027-12-18',next_review='2027-12-20' WHERE id=?",(b["id"],))
                arrange(conn,1)
            self.assertEqual(math._get(a["id"])["next_review"],"2027-12-16")
            self.assertEqual(math._get(b["id"])["next_review"],"2027-12-17")
            with db.connect() as conn: arrange(conn,1)
            self.assertEqual(math._get(a["id"])["next_review"],"2027-12-16")

    def test_finishing_on_last_workday_does_not_repeat_today(self):
        class Today(date):
            @classmethod
            def today(cls):
                return date(2027,12,17)
        with db.as_user(1),patch.object(math,"date",Today):
            p=math.create_problem("最后一天回顾",subject="高等数学")
            result=math.apply_result(p["id"],"right")
            self.assertFalse(result["next_review"])

    def test_past_exam_date_does_not_destroy_the_queue(self):
        with db.as_user(1):
            p=math.create_problem("保留队列",subject="高等数学")
            db.set_user_pref("exam_date","2026-01-01")
            self.assertTrue(math.api_due(module="math")["rest"])
            self.assertTrue(math._get(p["id"])["next_review"])
            db.set_user_pref("exam_date","2027-12-18")
            self.assertTrue(math.refresh_exam_review()["days_left"]>0)
            self.assertTrue(math._get(p["id"])["next_review"])


if __name__=="__main__":
    unittest.main(verbosity=2)
