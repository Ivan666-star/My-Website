#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把工程里的垃圾文件送进 **回收站**（可恢复，绝不硬删）。

用法：
    python tools/_cleanup_junk.py            # 只列出将要删除的东西（dry-run）
    python tools/_cleanup_junk.py --apply    # 真正删除（进回收站）

设计要点：
- 无版本控制（没有 .git），所以一律走 Windows 回收站（SHFileOperationW + FOF_ALLOWUNDO），
  不做不可恢复的删除。
- 逐条删除并逐条汇报，单条失败不影响其它条目。
- 只删「本脚本白名单」里写死的相对路径，绝不接受通配符 / 递归目录扫描。
"""
import ctypes
import os
import sys
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- 回收站
FO_DELETE = 3
FOF_ALLOWUNDO = 0x40
FOF_NOCONFIRMATION = 0x10
FOF_SILENT = 0x4
FOF_NOERRORUI = 0x400


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def _recycle_one(path: str) -> tuple[bool, str]:
    """把一个文件/目录送进回收站。返回 (ok, msg)。"""
    if not os.path.exists(path):
        return True, "已不存在（跳过）"
    p_from = os.path.abspath(path) + "\0\0"
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = p_from
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    try:
        ctypes.windll.shell32.SHFileOperationW.restype = ctypes.c_int
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    except Exception as e:  # pragma: no cover
        return False, f"调用失败: {e}"
    # ⚠️ 坑：这里 rc 经常回 2（ERROR_FILE_NOT_FOUND），但操作其实**成功了**。
    # 所以只认「源路径是否还活着」这个事实，不信 rc。
    if not os.path.exists(path):
        if op.fAnyOperationsAborted:
            return False, f"已消失但报中止(rc={rc})——请人工确认回收站"
        return True, "已进回收站"
    return False, f"仍在原地 (rc={rc})"


# ---------------------------------------------------------------- 白名单
CACHE = [
    "app/__pycache__",
    "tools/__pycache__",
]

SHOTS = [
    "data/_device_ipad_横.png",
    "data/_device_ipad_竖.png",
    "data/_device_iphone15.png",
    "data/_offline_state.png",
    "data/_probe_nav.png",
    "data/_seed_en_article_ipad.png",
    "data/_seed_en_materials_phone.png",
    "data/_seed_en_review_phone.png",
    "data/_seed_math_ipad.png",
    "data/_seed_math_phone.png",
    "data/_shot_math_刷题.png",
    "data/_shot_math_平板.png",
    "data/_shot_math_手机.png",
    "data/_shot_math_科目.png",
    "data/_shot_pen.png",
    "data/_shot_pen_trans.png",
]

# 保留最新一个安全备份 backup_pre_seeden_20261007_183815
BACKUPS = [
    "data/backup_pre_asserts_20261007_175512",
    "data/backup_pre_examfocus_20261005_125133",
    "data/backup_pre_examui_20261007_175846",
    "data/backup_pre_genre_20261004_194101",
    "data/backup_pre_mathtabs_examcount_20261007_174950",
    "data/backup_pre_multiuser_20261005_204017",
    "data/backup_pre_review_20261004_223912",
    "data/backup_pre_round4_20261007_171814",
    "data/backup_pre_seeden_20261007_183909",  # 与 183815 内容重复
    "data/backup_pre_subjects_trim_20261007_182734",
    "data/backup_pre_topbar_scope_20261007_174128",
    "data/backup_pre_v2_20261004_191121",
]

STALE = [
    ".env.bak_20261007",
    "启动.bat.bak",
    "data/math_images/1",  # 2 张 jpg，math_problems.image_path 全空 → 孤儿资源
]

# 一次性脚本：使命已完成，代码里再无引用（无 git，故走回收站可恢复）
LEGACY_TOOLS = [
    "tools/_patch_asserts_examui.py",
    "tools/_patch_asserts_mathtabs.py",
    "tools/_patch_draft_aiclass.py",
    "tools/_patch_frontend.py",
    "tools/_patch_math_delims.py",
    "tools/_patch_mathtabs_examcount.py",
    "tools/_patch_round4.py",
    "tools/_patch_shot_math.py",
    "tools/_patch_shot_math2.py",
    "tools/_patch_shot_math3.py",
    "tools/_patch_shot_math4.py",
    "tools/_patch_shot_math5.py",
    "tools/_patch_shot_math6.py",
    "tools/_patch_shot_math7.py",
    "tools/_patch_shot_round4.py",
    "tools/_patch_subjects_trim.py",
    "tools/_patch_topbar_scope.py",
    "tools/_new_math_section.js",
    "tools/bat_to_gbk.py",
    "tools/_probe_nav.py",
    "tools/_diag_report.py",
    "tools/_shots.py",
    "tools/_demo_new_pipeline.py",
    "tools/_migrate_test.py",
    "tools/_review_test.py",
    "tools/_shot_account.py",
]

GROUPS = [
    ("缓存", CACHE),
    ("测试截图（可重新生成）", SHOTS),
    ("旧备份快照（保留 183815）", BACKUPS),
    ("陈旧快照 / 孤儿资源", STALE),
    ("一次性脚本（已完成使命）", LEGACY_TOOLS),
]


def main() -> int:
    apply = "--apply" in sys.argv
    # --only 1,2  只处理指定分组（1 起算）；不传则全部。用于拆小批量，避免一次删太多。
    only = None
    for i, a in enumerate(sys.argv):
        if a == "--only" and i + 1 < len(sys.argv):
            only = {int(x) for x in sys.argv[i + 1].split(",") if x.strip()}

    total = ok = skipped = failed = 0
    for gi, (gname, items) in enumerate(GROUPS, start=1):
        if only is not None and gi not in only:
            continue
        print(f"\n===== {gname} =====")
        for rel in items:
            total += 1
            abspath = os.path.join(ROOT, rel.replace("/", os.sep))
            if not apply:
                mark = "存在" if os.path.exists(abspath) else "缺失"
                print(f"  [dry] {rel}  ({mark})")
                continue
            good, msg = _recycle_one(abspath)
            if good:
                if "跳过" in msg:
                    skipped += 1
                    print(f"  [--]  {rel}  {msg}")
                else:
                    ok += 1
                    print(f"  [OK]  {rel}  {msg}")
            else:
                failed += 1
                print(f"  [ERR] {rel}  {msg}")

    if not apply:
        print(f"\n共 {total} 个目标。加 --apply 才会真正删除（进回收站）。")
        return 0

    print(f"\n合计 {total}：进回收站 {ok} / 跳过 {skipped} / 失败 {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
