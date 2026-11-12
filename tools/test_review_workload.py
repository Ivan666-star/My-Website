"""增长题库的每日建议量、跨科目额度、进度稳定及分页验证；仅临时数据。"""
import sys
import unittest
from datetime import date,timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_focus_workflow as fixtures
from app import db,math
from app.review_workload import daily_selection


class Workload(unittest.TestCase):
    setUp=fixtures.FocusWorkflow.setUp
    tearDown=fixtures.FocusWorkflow.tearDown

    def seed(self,count):
        with db.as_user(1):
            db.set_user_pref("review_minutes_min","45")
            db.set_user_pref("review_minutes_max","60")
            with db.connect() as conn:
                conn.executemany("INSERT INTO math_problems(user_id,stem,answer,subject,planned_review,next_review) "
                                 "VALUES(1,?,'答案',?,'2026-01-01','2026-01-01')",
                                 [(f"临时测试题{i}","信号与系统" if i==count-1 else "高等数学") for i in range(count)])

    def test_5000_due_items_share_one_budget_and_remain_accessible(self):
        self.seed(5000)
        with db.as_user(1):
            a=math.api_due(module="math")
            b=math.api_due(module="sig")
            self.assertEqual(a["daily"]["capacity"],8)
            self.assertEqual(len(a["items"])+len(b["items"]),8)
            self.assertEqual(a["pending"]+b["pending"],5000)
            self.assertEqual(len(b["items"]),1)
            self.assertEqual(a["daily"]["ids"],b["daily"]["ids"])
            self.assertEqual(a["daily"]["ids"],math.api_due(module="math")["daily"]["ids"])
            self.assertGreater(len(math.api_due(module="math",all_due=1)["items"]),8)

    def test_completion_and_optional_practice_consume_shared_daily_budget(self):
        self.seed(30)
        with db.as_user(1):
            first=math.api_due(module="math")
            selected=first["daily"]["ids"]
            math.apply_result(selected[0],"right")
            after=math.api_due(module="math")
            self.assertEqual(after["daily"]["completed"],1)
            self.assertEqual(after["daily"]["remaining"],7)
            self.assertEqual(after["daily"]["ids"],selected[1:])
            extra=next(r["id"] for r in math.list_problems() if r["id"] not in selected)
            math.apply_result(extra,"wrong")
            after=math.api_due(module="math")
            self.assertEqual(after["daily"]["completed"],2)
            self.assertEqual(after["daily"]["remaining"],6)

    def test_weekend_is_optional_and_next_workday_has_fresh_budget(self):
        self.seed(20)
        with db.as_user(1):
            saturday=daily_selection(date(2026,11,14),date(2027,12,18))
            self.assertTrue(saturday["rest"])
            self.assertEqual(saturday["ids"],[])
            monday=daily_selection(date(2026,11,16),date(2027,12,18))
            self.assertFalse(monday["rest"])
            self.assertEqual(len(monday["ids"]),8)

    def test_old_items_are_not_starved_and_users_are_isolated(self):
        self.seed(40)
        with db.as_user(1):
            day=daily_selection(date.today(),date(2027,12,18))
            self.assertIn(1,day["ids"])
        with db.as_user(2):
            other=daily_selection(date.today(),date(2027,12,18))
            self.assertEqual(other["ids"],[])
            self.assertEqual(other["completed"],0)

    def test_estimated_duration_changes_suggestion_not_question_count(self):
        self.seed(40)
        with db.as_user(1):
            db.set_user_pref("review_item_minutes","10")
            day=daily_selection(date.today(),date(2027,12,18))
            self.assertEqual(day["capacity"],5)
            self.assertEqual(len(day["ids"]),5)
            self.assertEqual(math.stats()["total"],40)

    def test_all_pages_keep_old_items_reachable(self):
        self.seed(505)
        with db.as_user(1):
            first=math.api_list(module="math",limit=100,offset=0)
            last=math.api_list(module="math",limit=100,offset=500)
            self.assertEqual(first["total"],504)
            self.assertEqual(first["next_offset"],100)
            self.assertEqual(len(first["items"]),100)
            self.assertEqual(len(last["items"]),4)
            self.assertIsNone(last["next_offset"])
            self.assertIn(1,[p["id"] for p in last["items"]])

    def test_calendar_summary_does_not_download_whole_question_bank(self):
        self.seed(5000)
        with db.as_user(1):
            day=(date.today()+timedelta(days=1)).isoformat()
            db.execute("UPDATE math_problems SET next_review=?,planned_review=? WHERE user_id=1",(day,day))
            plan=math.api_review_plan(module="math",days=14,summary=1)
            self.assertEqual(sum(d["count"] for d in plan["days"]),4999)
            self.assertTrue(all(d["items"]==[] for d in plan["days"]))


if __name__=="__main__":
    unittest.main(verbosity=2)
