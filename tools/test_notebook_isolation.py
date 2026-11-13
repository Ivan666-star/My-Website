"""通过真实 HTTP 路由验证两科读写边界；只操作临时账号和数据库。"""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_focus_workflow as fixtures
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import db, math


class NotebookIsolation(unittest.TestCase):
    def setUp(self):
        fixtures.FocusWorkflow.setUp(self)
        app = FastAPI()
        @app.middleware("http")
        async def user_scope(request, call_next):
            with db.as_user(1):
                return await call_next(request)
        app.include_router(math.router)
        self.client = TestClient(app)
        with db.as_user(1):
            self.math = math.create_problem("数学题", subject="高等数学", answer="1")
            self.sig = math.create_problem("信号题", subject="信号与系统", answer="2")

    def tearDown(self):
        self.client.close()
        fixtures.FocusWorkflow.tearDown(self)

    def test_every_item_route_rejects_the_other_notebook(self):
        for module, problem in (("math", self.sig), ("sig", self.math)):
            url = f'/api/math/problems/{problem["id"]}'
            with self.subTest(module=module):
                for method, suffix, body in (("GET", "", None), ("PATCH", "", {"answer":"污染"}),
                        ("DELETE", "", None), ("GET", "/image", None),
                        ("POST", "/result", {"result":"wrong"}), ("POST", "/answer", None),
                        ("POST", "/analyze", None)):
                    r = self.client.request(method, url+suffix+"?module="+module, json=body)
                    self.assertEqual(r.status_code, 404, (method, suffix, r.text))
        with db.as_user(1):
            self.assertEqual(math._get(self.math["id"])["answer"], "1")
            self.assertEqual(math._get(self.sig["id"])["answer"], "2")
            self.assertEqual(math.review_log(self.math["id"]), [])

    def test_writes_cannot_reclassify_into_the_other_notebook(self):
        for module, subject in (("math", "信号与系统"), ("sig", "高等数学")):
            r = self.client.post("/api/math/problems?module="+module, json={"stem":"不应保存", "subject":subject})
            self.assertEqual(r.status_code, 400)
        r = self.client.patch(f'/api/math/problems/{self.math["id"]}?module=math', json={"subject":"信号与系统"})
        self.assertEqual(r.status_code, 400)

    def test_metadata_counts_random_and_search_only_show_current_notebook(self):
        for module, subject, expected in (("math", "高等数学", self.math), ("sig", "信号与系统", self.sig)):
            meta = self.client.get("/api/math/meta?module="+module).json()
            self.assertEqual(meta["stats"]["total"], 1)
            self.assertIn(subject, meta["subjects"])
            other = "信号与系统" if module == "math" else "高等数学"
            self.assertNotIn(other, meta["subjects"])
            self.assertNotIn(other, meta["topics"])
            self.assertEqual(self.client.get("/api/math/random?module="+module).json()["problem"]["id"], expected["id"])
        self.assertEqual(self.client.get("/api/math/problems").json()["total"], 1)
        self.assertEqual(self.client.get("/api/math/problems?module=unknown").status_code, 400)

    def test_import_retries_and_extraction_jobs_are_scoped_to_notebook(self):
        for module in ("math", "sig"):
            payload = {"stem":"各科的独立新题", "request_id":"same-request"}
            a = self.client.post("/api/math/problems?module="+module, json=payload)
            b = self.client.post("/api/math/problems?module="+module, json=payload)
            self.assertEqual(a.status_code, 200, a.text)
            self.assertEqual(a.json()["problem"]["id"], b.json()["problem"]["id"])
            if module == "sig":
                self.assertEqual(a.json()["problem"]["subject"], "信号与系统")
        job_id = "a"*32
        with patch.object(math, "extract_from_image", return_value={"stem":"识别测试题", "subject":"高等数学"}):
            self.client.post("/api/math/extract/jobs?module=math", json={"request_id":job_id, "image":"data:image/png;base64,AA=="})
            self.assertEqual(self.client.get("/api/math/extract/jobs/"+job_id+"?module=sig").status_code, 404)
            for _ in range(100):
                if not self.client.get("/api/math/extract/jobs/"+job_id+"?module=math").json()["running"]:
                    break
                time.sleep(.01)

    def test_vision_prompt_is_scoped_and_cannot_silently_move_questions(self):
        self.assertNotIn("信号与系统", math.build_extract_prompt(module="math"))
        self.assertNotIn("高等数学", math.build_extract_prompt(module="sig"))
        with patch.object(math, "get_provider") as provider:
            provider.return_value.chat_vision.return_value = '{"stem":"一题", "subject":"信号与系统"}'
            with self.assertRaises(math.AIError):
                math.extract_from_image("data:image/png;base64,AA==", module="math")


if __name__ == "__main__":
    unittest.main(verbosity=2)
