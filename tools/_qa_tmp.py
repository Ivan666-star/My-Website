# -*- coding: utf-8 -*-
"""测试脚手架共用的收尾工具：删临时目录要重试，删不掉就等进程退出再删。

为什么需要它（本项目踩过）：
  Windows 上 `shutil.rmtree(path, ignore_errors=True)` 常常**静默失败**：
    · uvicorn 子进程刚被 terminate，句柄还没释放；
    · `app/db.py` 的连接按引用计数回收 —— 只要还有一处引用活着，`.db` 就被占着。
  结果是临时目录留在 %TEMP% 里，每跑一次测试多几个（实测攒了 60+ 个：
  pentest / prism_reg / efview / mu_mig …）。

设计：调用方只管喊 `cleanup(tmp)`，本模块负责
  ① 先立刻试几次（大多数情况这就够了）；
  ② 还删不掉就登记到 `atexit`，等进程要退出、句柄都放掉之后再删一遍。
这样调用点不用关心"谁还占着"，也不会因为装没事而悄悄攒垃圾。
"""
from __future__ import annotations

import atexit
import shutil
import time
from pathlib import Path

_PENDING: list[Path] = []


def _try(path: Path, tries: int = 6, delay: float = 0.3) -> bool:
    for _ in range(tries):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return True
        time.sleep(delay)
    return not path.exists()


@atexit.register
def _flush() -> None:
    """进程要退了：这时 sqlite 连接多半已被回收，再删一遍。

    ⚠️ 先 gc.collect()：连接对象可能还挂在某个引用环/异常回溯里，
    不主动回收的话 `os.remove` 依旧被句柄挡住（实测 mu_mig 就栽在这上面）。
    """
    import gc

    gc.collect()
    for p in list(_PENDING):
        if _try(p, tries=6, delay=0.4):
            _PENDING.remove(p)
    for p in _PENDING:
        print(f"⚠️ 临时目录始终没能删掉，请手动清理：{p}")


def cleanup(path, tries: int = 6, delay: float = 0.3) -> bool:
    """删掉临时目录，返回"现在是否已经删干净"。删不掉也不会丢，会留到进程退出时再删。"""
    p = Path(path)
    if _try(p, tries=tries, delay=delay):
        return True
    if p not in _PENDING:
        _PENDING.append(p)
    return False
