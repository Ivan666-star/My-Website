# -*- coding: utf-8 -*-
"""真题导入 / 素材搜索 / 复习提醒 —— 端到端接口回归（打真实运行中的服务）。

用法：
    python tools/_api_test.py [base_url]
默认 base_url = http://127.0.0.1:8848

如果这个实例是「账号模式」（线上就是），需要带上账号才跑得动：
    QA_USER=Ivan.666 QA_PASS=你的密码 python tools/_api_test.py
没给凭据时会**直接跳过**，而不是报一屏 401 假失败 —— 这是手工冒烟测试，
前提是"能免登录访问"，账号模式下本来就不成立。
"""
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

FLAGS = [a for a in sys.argv[1:] if a.startswith("--")]
POS = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE = (POS[0] if POS else "http://127.0.0.1:8848").rstrip("/")

QA_USER = os.environ.get("QA_USER", "")
QA_PASS = os.environ.get("QA_PASS", "")

ok = 0
fail = 0

# 会话 cookie 要跨请求带着，所以统一走一个 opener
_JAR = http.cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_JAR),
    urllib.request.ProxyHandler({}),
)


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [PASS] {label} {extra}".rstrip())
    else:
        fail += 1
        print(f"  [FAIL] {label} {extra}".rstrip())


def call(method, path, body=None, timeout=300, form=False):
    """返回 (status, json_or_text)。form=True 时按表单编码（登录接口用）。"""
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        status = e.code
    try:
        return status, json.loads(raw)
    except Exception:  # noqa: BLE001
        return status, raw


def ensure_access() -> bool:
    """免登录实例直接开工；账号模式则尝试登录，没凭据就诚实跳过。"""
    st, _ = call("GET", "/api/bootstrap")
    if st == 200:
        return True
    if not (QA_USER and QA_PASS):
        print(f"\n跳过：{BASE} 需要登录（账号模式），但没给凭据。")
        print("  带上账号再跑：QA_USER=用户名 QA_PASS=密码 python tools/_api_test.py")
        return False
    st2, _ = call("POST", "/login", body={
        "username": QA_USER, "password": QA_PASS, "next": "/"}, form=True)
    if st2 not in (200, 303):
        print(f"\n跳过：用 QA_USER={QA_USER} 登录失败（status={st2}）。")
        return False
    st3, _ = call("GET", "/api/bootstrap")
    if st3 != 200:
        print(f"\n跳过：登录后仍拿不到数据（status={st3}）。")
        return False
    print(f"   （已用 {QA_USER} 登录）")
    return True


print("=" * 64)
print(f"接口回归：{BASE}")
print("=" * 64)

if not ensure_access():
    sys.exit(0)

# ---------------------------------------------------------------
print("\n【1】基础可用性 / 每日内容不受影响")
# ---------------------------------------------------------------
st, boot = call("GET", "/api/bootstrap")
check("GET /api/bootstrap 200", st == 200, f"({st})")
meta = (boot or {}).get("meta") if isinstance(boot, dict) else None
check("bootstrap 返回 meta", isinstance(meta, dict))
check("meta 带 review_due 字段", isinstance(meta, dict) and "review_due" in meta,
      f"(review_due={meta.get('review_due') if isinstance(meta, dict) else 'N/A'})")

st, recent = call("GET", "/api/bootstrap")
recent = (recent or {}).get("history") if isinstance(recent, dict) else None
check("bootstrap.history 可解析", isinstance(recent, list), f"({len(recent) if isinstance(recent, list) else '?'} 条)")
if isinstance(recent, list):
    bad = [a for a in recent if str(a.get("date", "")).startswith("exam-")]
    check("每日内容列表里没有混入真题", not bad, f"(混入 {len(bad)} 条)")
    check("每日内容列表仍是真实日期",
          all(isinstance(a.get("date"), str) and len(a["date"]) == 10 for a in recent),
          f"({len(recent)} 条)")

# ---------------------------------------------------------------
print("\n【2】素材搜索（前端本地过滤，这里校验数据源字段齐全）")
# ---------------------------------------------------------------
st, favs = call("GET", "/api/favorites")
check("GET /api/favorites 200", st == 200, f"({st})")
items = (favs or {}).get("items") if isinstance(favs, dict) else favs
check("素材列表可解析", isinstance(items, list), f"({len(items) if isinstance(items, list) else '?'} 条)")
if isinstance(items, list) and items:
    need = ("content", "cn", "example", "usage", "category", "tags", "genre", "position")
    f0 = items[0]
    missing = [k for k in need if k not in f0]
    check("搜索要用的字段都在", not missing, f"(缺 {missing})")
    check("tags 已解析成数组", isinstance(f0.get("tags"), list), f"({type(f0.get('tags')).__name__})")
    check("复习字段已随行返回", "review_count" in f0 and "next_review" in f0)
    # 用第一条素材的真实内容做一次「服务端数据能不能支撑搜索」的核对
    kw = str(f0.get("content") or "")[:4]
    if kw:
        hit = [m for m in items if kw.lower() in json.dumps(m, ensure_ascii=False).lower()]
        check(f"关键字「{kw}」能命中至少 1 条", len(hit) >= 1, f"(命中 {len(hit)})")

# ---------------------------------------------------------------
print("\n【3】复习提醒")
# ---------------------------------------------------------------
st, rev = call("GET", "/api/review/due")
check("GET /api/review/due 200", st == 200, f"({st})")
check("返回 items/stats/intervals/mastered_at",
      all(k in rev for k in ("items", "stats", "intervals", "mastered_at")),
      f"({sorted(rev.keys()) if isinstance(rev, dict) else '?'})")
check("intervals 阶梯正确", rev.get("intervals") == [1, 2, 4, 7, 15, 30, 60],
      f"({rev.get('intervals')})")
check("mastered_at 与阶梯长度一致", rev.get("mastered_at") == 7, f"({rev.get('mastered_at')})")
stats = rev.get("stats") or {}
check("stats 字段齐全", all(k in stats for k in ("due", "in_cycle", "mastered", "total")),
      f"({stats})")

# 非法 result
st, err = call("POST", "/api/review/1/result", {"result": "哈哈哈"})
check("非法 result 返回 400", st == 400, f"({st})")
# 不存在的 id
st, err = call("POST", "/api/review/999999/result", {"result": "known"})
check("不存在的素材返回 404", st == 404, f"({st})")

# 对一条真实素材跑一遍 known → 观察间隔推进 → 复位
if isinstance(items, list) and items:
    fid = items[0]["id"]
    before = call("GET", "/api/review/due")[1]["stats"]
    st, r1 = call("POST", f"/api/review/{fid}/result", {"result": "readd"})
    check("readd 成功", st == 200 and r1.get("ok"), f"({st})")
    st, r2 = call("POST", f"/api/review/{fid}/result", {"result": "known"})
    check("known 成功且返回更新后的素材", st == 200 and r2.get("item"), f"({st})")
    check("known 后 next_review 是明天",
          r2["item"]["next_review"] > r2["stats"]["today"] if isinstance(r2, dict) else False,
          f"(next_review={r2.get('item', {}).get('next_review') if isinstance(r2, dict) else '?'})")
    check("接口返回同步的 stats", isinstance(r2.get("stats"), dict), f"({r2.get('stats')})")
    # 复位，别影响用户真实进度
    call("POST", f"/api/review/{fid}/result", {"result": "readd"})
    after = call("GET", "/api/review/due")[1]["stats"]
    check("复位后 due 数量与测试前一致", after["due"] == before["due"],
          f"({before['due']} → {after['due']})")

# ---------------------------------------------------------------
print("\n【4】真题导入")
# ---------------------------------------------------------------
st, ex = call("GET", "/api/exam/list")
check("GET /api/exam/list 200", st == 200, f"({st})")
check("返回 items + slots + meta", all(k in ex for k in ("items", "slots", "meta")),
      f"({sorted(ex.keys())})")
slots = ex.get("slots") or {}
check("slots 覆盖 7 个篇目", len(slots) == 7, f"({len(slots)}: {list(slots)})")
meta = ex.get("meta") or {}
tgt = meta.get("target_year")
check("meta 带目标考试年份", isinstance(tgt, int) and tgt >= 2026, f"(target_year={tgt})")
check("最新真题年份 = 目标年份 - 1", meta.get("latest_year") == (tgt - 1 if tgt else None),
      f"(latest_year={meta.get('latest_year')})")
check("红线起点 = 最新年份 - 2", meta.get("redline_from") == (meta.get("latest_year", 0) - 2),
      f"(redline_from={meta.get('redline_from')})")
yrs = meta.get("years") or []
check("年份下拉覆盖到最新一年", yrs and yrs[0] == meta.get("latest_year"),
      f"(首项 {yrs[0] if yrs else None})")
check("年份下拉覆盖到 1998", yrs and yrs[-1] == 1998, f"(末项 {yrs[-1] if yrs else None})")
check("年份下拉是降序无重复", yrs == sorted(set(yrs), reverse=True), f"(共 {len(yrs)} 项)")
n_exam_before = len(ex.get("items") or [])
print(f"  （当前已导入真题 {n_exam_before} 篇）")

st, _ = call("GET", "/api/exam/999999")
check("不存在的真题返回 404", st == 404, f"({st})")

# 校验：正文太短
st, _ = call("POST", "/api/exam/import", {"text": "too short", "year": 2010, "slot": "text2"})
check("正文过短返回 400", st == 400, f"({st})")
# 校验：年份越界
long_enough = "The quick brown fox jumps over the lazy dog. " * 10
st, _ = call("POST", "/api/exam/import", {"text": long_enough, "year": 1900, "slot": "text2"})
check("年份 1900 返回 400", st == 400, f"({st})")
st, _ = call("POST", "/api/exam/import", {"text": long_enough, "year": 2099, "slot": "text2"})
check("年份 2099 返回 400", st == 400, f"({st})")
# 校验：篇目非法
st, _ = call("POST", "/api/exam/import", {"text": long_enough, "year": 2010, "slot": "text9"})
check("非法篇目返回 400", st == 400, f"({st})")
st, _ = call("POST", "/api/exam/import", {"text": long_enough, "year": 2010})
check("缺篇目返回 400", st == 400, f"({st})")

# ---------------------------------------------------------------
if "--no-ai" in sys.argv:
    print("\n【5】跳过真实 AI 导入（--no-ai）")
else:
    print("\n【5】真实导入一篇真题（会调用 AI，稍慢）")
    SAMPLE = """
In the 20th century, the idea that the universe had a beginning was regarded by many
scientists as philosophically unacceptable. A universe with a beginning seemed to imply
a creator, and that implication was unwelcome in a discipline that prided itself on
explaining nature without recourse to the supernatural. Yet the evidence accumulated
steadily, and by the 1960s the steady-state theory, which held that matter is continuously
created so that the universe looks the same at all times, had been abandoned by most
physicists. The discovery of the cosmic microwave background radiation was decisive: it
was the afterglow of a hot, dense early state, and it could not be explained by any
model in which the universe had always existed in its present form. What is instructive
here is not simply that the evidence won. It is that a theory can be defended for
decades on grounds that have little to do with observation, and that the resistance to
a new idea is often strongest when the idea carries implications that reach beyond
science itself. Historians of science have argued that this episode illustrates a
general pattern, in which theoretical commitments function as much as social and
psychological ones, shaping which questions are asked and which anomalies are tolerated.
"""

    st, imp = call("POST", "/api/exam/import", {
        "text": SAMPLE, "year": 2010, "slot": "text2", "title": "宇宙是否有开端",
    })
    if st == 200 and isinstance(imp, dict) and imp.get("ok"):
        art = imp.get("article") or {}
        bundle = imp.get("bundle") or {}
        print(f"  导入成功：id={art.get('id')} date={art.get('date')} title={art.get('title')}")
        check("返回 article", bool(art.get("id")))
        check("date 是伪日期 exam-2010-text2", art.get("date") == "exam-2010-text2",
              f"({art.get('date')})")
        check("source_type = exam", art.get("source_type") == "exam", f"({art.get('source_type')})")
        check("返回的精读包里有词汇", bool(bundle.get("vocab")),
              f"({len(bundle.get('vocab') or [])} 个)")
        check("返回的精读包里有长难句", bool(bundle.get("sentences")),
              f"({len(bundle.get('sentences') or [])} 句)")
        check("返回的精读包里有语法点", bool(bundle.get("grammar")),
              f"({len(bundle.get('grammar') or [])} 个)")

        eid = art.get("id")
        st, b2 = call("GET", f"/api/exam/{eid}")
        check("GET /api/exam/{id} 200", st == 200, f"({st})")
        check("能按 id 重新取回同一篇", (b2.get("article") or {}).get("id") == eid)

        st, ex2 = call("GET", "/api/exam/list")
        n_after = len(ex2.get("items") or [])
        # 同一篇目重复导入是覆盖而非新增，所以允许不变
        check("真题列表未丢内容（覆盖或 +1）", n_after in (n_exam_before, n_exam_before + 1),
              f"({n_exam_before} → {n_after}，重复导入同篇目为覆盖)")
        check("同一 date 只保留一条",
              sum(1 for x in (ex2.get("items") or []) if x["date"] == "exam-2010-text2") == 1)
        row = next((x for x in ex2["items"] if x["id"] == eid), None)
        check("列表中带 vocab_count / sent_count",
              row is not None and "vocab_count" in row and "sent_count" in row,
              f"({row and {k: row[k] for k in ('vocab_count', 'sent_count')}})")

        # 真题不应污染每日内容
        st, boot2 = call("GET", "/api/bootstrap")
        recent2 = (boot2 or {}).get("history") or []
        check("导入真题后每日内容列表仍无真题",
              not [a for a in recent2 if str(a.get("date", "")).startswith("exam-")],
              f"({len(recent2)} 条)")
        st, rv2 = call("GET", "/api/review/due")
        check("真题导入后复习接口仍正常", st == 200, f"({st})")
    else:
        check("真实导入成功", False, f"({st}) {str(imp)[:180]}")

print("-" * 64)
print(f"结果：{ok} 通过 / {fail} 失败")
print("-" * 64)
sys.exit(1 if fail else 0)
