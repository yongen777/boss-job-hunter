"""跑 tests/ 下所有 test_*.py，用子进程隔离——一个炸了不影响别的。

用法：
    python tests/run.py

退出码 0 = 全绿；1 = 有不绿的（会点名是哪几个）。
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent


def main() -> int:
    files = sorted(HERE.glob("test_*.py"))
    if not files:
        print("[!] tests/ 下面没有 test_*.py")
        return 1

    failed: list[str] = []
    for f in files:
        print(f"\n===== {f.name} =====", flush=True)
        proc = subprocess.run([sys.executable, str(f)], cwd=str(ROOT))
        if proc.returncode != 0:
            failed.append(f"{f.name} (exit {proc.returncode})")

    print("\n---- 汇总 ----")
    if failed:
        print(f"[x] {len(failed)}/{len(files)} 个没过：{', '.join(failed)}")
        return 1
    print(f"[ok] {len(files)} 个测试全过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())