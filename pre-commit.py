# -*- coding: utf-8 -*-

import subprocess
import sys


def run(command: list[str]) -> int:
    print(f"$ {' '.join(command)}")
    return subprocess.call(command)


def main() -> int:
    commands = [
        ["ruff", "check", "."],
        [sys.executable, "-m", "compileall", "magazarr", "tests"],
        [sys.executable, "-m", "pytest"],
    ]
    for command in commands:
        code = run(command)
        if code:
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
