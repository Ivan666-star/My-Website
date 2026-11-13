"""用无头 Chrome 检查题库切换、独立草稿和到期分页；全部使用临时数据。"""
import gc
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app import db, users, math
    from app.config import settings
    from playwright.sync_api import sync_playwright
    import uvicorn

    original = settings.db_path
    original_english = settings.english_enabled
    with tempfile.TemporaryDirectory(prefix="prism_notebook_ui_") as temp:
        settings.db_path = Path(temp) / "app.db"
        settings.english_enabled = False
        db._initialized_user_dbs.clear()
        db.init_db()
        with db.as_system():
            user = users.create_user("notebook_test", "temporary-test-password", is_admin=True)
            users.set_approved(user["id"], True)
        with db.as_user(user["id"]), db.connect() as conn:
            conn.executemany("INSERT INTO math_problems(user_id,subject,stem,answer,next_review,planned_review) "
                "VALUES(?,?,?,'最终答案','2026-01-01','2026-01-01')",
                [(user["id"], "高等数学", f"数学测试{i}") for i in range(45)] +
                [(user["id"], "信号与系统", f"信号测试{i}") for i in range(2)])
        conn.close()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        from app.main import app
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        base = f"http://127.0.0.1:{port}"
        errors = []
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
                page = browser.new_page(viewport={"width": 1100, "height": 900})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base+"/login")
                page.wait_for_load_state("networkidle")
                page.locator('[name="username"]').fill("notebook_test")
                page.locator('[name="password"]').fill("temporary-test-password")
                page.locator('button[type="submit"]').click()
                page.wait_for_load_state("networkidle")

                def enter(module):
                    page.goto(base+"/#"+module)
                    page.wait_for_load_state("networkidle")
                    page.wait_for_function("m => MATH.module === m && MATH.meta?.module === m", arg=module)

                def draft(text):
                    page.locator("#mathManualBtn").click()
                    page.locator(".math-draft-item .draft-src summary").click()
                    page.locator(".math-draft-stem").fill(text)

                enter("math")
                assert "信号与系统" not in page.locator("#mfSubject").inner_text()
                assert page.locator(".math-due-card").count() == 20
                page.locator("#mathDueMore").click()
                page.wait_for_function("MATH.due.length === 40")
                page.locator("#mathDueMore").click()
                page.wait_for_function("MATH.due.length === 45")
                assert page.locator("#mathDueMore").count() == 0
                draft("数学独立草稿")
                enter("sig")
                assert "高等数学" not in page.locator("#mfSubject").inner_text()
                assert page.locator(".math-draft-item").count() == 0
                assert page.locator(".math-due-card").count() == 2
                draft("信号独立草稿")
                enter("math")
                assert page.locator(".math-draft-item").count() == 1
                assert page.locator(".math-draft-stem").input_value() == "数学独立草稿"
                page.locator("#mathSaveAll").click()
                page.wait_for_function("!MATH.savingModules.has('math') && !notebookDrafts().length")
                enter("sig")
                assert page.locator(".math-draft-item").count() == 1
                assert page.locator(".math-draft-stem").input_value() == "信号独立草稿"
                assert page.evaluate("MATH.meta.stats.total") == 2
                enter("math")
                page.locator('[data-due-res="right"]').first.click()
                page.wait_for_function("MATH.daily.completed === 1 && MATH.pending === 44")
                enter("sig")
                assert page.evaluate("MATH.daily.completed") == 0
                assert page.evaluate("MATH.pending") == 2
                # 数学请求迟到时，信号页面不能被旧题覆盖。
                enter("math")
                old_problem = page.evaluate("MATH.items[0]")
                held = []
                page.route("**/api/math/random?module=math", lambda route: held.append(route))
                page.evaluate("() => { mathRandom(); }")
                page.wait_for_function("MATH.module === 'math'")
                for _ in range(50):
                    if held:
                        break
                    page.wait_for_timeout(20)
                assert held
                page.evaluate("location.hash = '#sig'")
                page.wait_for_function("MATH.module === 'sig' && MATH.meta?.module === 'sig'")
                held[0].fulfill(json={"ok": True, "problem": old_problem})
                page.wait_for_function("document.body.innerText.includes('页面已切换')")
                assert page.evaluate("MATH.drill === null")
                assert page.locator(".math-draft-stem").input_value() == "信号独立草稿"
                assert not errors, errors
                browser.close()
            print("PASS: 科目选项、切换、独立草稿、单科保存、45题连续加载、独立复习进度、旧响应拦截；无页面脚本异常")
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            settings.db_path = original
            settings.english_enabled = original_english
            db._initialized_user_dbs.clear()
            gc.collect()


if __name__ == "__main__":
    main()
