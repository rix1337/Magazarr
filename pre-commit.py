import json
import os
import re
import subprocess
import sys
from pathlib import Path

VERSION_FILE = Path("magazarr/version.py")


def run(cmd, check=True, capture=False, text=True):
    print(f"⚙️  Exec: {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=capture, text=text)


def get_env(key, default=None):
    return os.environ.get(key, default)


def git_status_has_changes():
    return bool(run(["git", "status", "--porcelain"], capture=True).stdout.strip())


def task_format():
    print("\n🔍 --- 1. FORMATTING ---")

    result = run(["uv", "run", "ruff", "check", "--fix", "."], check=False)
    if result.returncode != 0:
        print("❌ Critical errors found. Fix them before staging.")
        sys.exit(1)

    run(["uv", "run", "ruff", "format", "."], check=False)

    if git_status_has_changes():
        print("✅ Linting fixes applied.")
        run(["git", "add", "."])
        return True

    print("✨ Code style is already perfect.")
    return False


def task_tests():
    print("\n🧪 --- 2. TESTS ---")
    result = run(["uv", "run", "pytest"], check=False)
    if result.returncode != 0:
        print("❌ Tests failed. Fix before staging.")
        sys.exit(1)
    print("✅ Tests passed.")


def task_version_bump():
    print("\n🏷️  --- 3. VERSION CHECK ---")
    new_v = ""

    def get_ver(content):
        m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
        return m.group(1) if m else None

    def bump(v):
        p = v.split(".")
        while len(p) < 3:
            p.append("0")
        try:
            p[-1] = str(int(p[-1]) + 1)
        except Exception:
            p.append("1")
        return ".".join(p)

    def ver_tuple(v):
        try:
            return tuple(map(int, v.split(".")))
        except Exception:
            return (0, 0, 0)

    try:
        remote_ref = "origin/main"
        has_origin = (
            subprocess.run(
                ["git", "remote", "get-url", "origin"],
                check=False,
                capture_output=True,
                text=True,
            ).returncode
            == 0
        )

        if has_origin:
            run(["git", "fetch", "origin", "main"], check=False)
            remote_ok = (
                subprocess.run(
                    ["git", "rev-parse", "--verify", remote_ref],
                    check=False,
                    capture_output=True,
                ).returncode
                == 0
            )
            if not remote_ok:
                print("ℹ️  origin/main not available. Skipping version comparison.")
                remote_ref = None
        else:
            print("ℹ️  No 'origin' remote. Skipping version comparison.")
            remote_ref = None

        main_v = None
        if remote_ref:
            try:
                main_v_content = subprocess.check_output(
                    ["git", "show", f"{remote_ref}:{VERSION_FILE.as_posix()}"],
                    text=True,
                )
                main_v = get_ver(main_v_content)
            except Exception:
                pass

        curr_v = get_ver(VERSION_FILE.read_text())
        print(f"📊 Main: {main_v} | Current: {curr_v}")

        if main_v and curr_v and ver_tuple(curr_v) <= ver_tuple(main_v):
            new_v = bump(main_v)
            print(f"🚀 Bumping version to: {new_v}")
            content = VERSION_FILE.read_text().replace(f'"{curr_v}"', f'"{new_v}"')
            VERSION_FILE.write_text(content)
            run(["git", "add", "."])
            return True, new_v

    except Exception as e:
        print(f"⚠️  Version check warning (non-fatal): {e}")

    return False, new_v


def main():
    is_ci = "--ci" in sys.argv

    fixed_format = task_format()
    task_tests()
    fixed_version, new_v = task_version_bump()

    if is_ci and (fixed_format or fixed_version):
        print("\n📤 --- 4. PUSH ---")

        run(["git", "config", "--global", "user.name", "github-actions[bot]"])
        run(
            [
                "git",
                "config",
                "--global",
                "user.email",
                "41898282+github-actions[bot]@users.noreply.github.com",
            ]
        )

        parts = []
        if fixed_format:
            parts.append("fixed linting")
        if fixed_version:
            parts.append(f"increased version to {new_v}")

        msg_body = (
            ", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0]
        )

        try:
            run(["git", "commit", "-m", f"chore: 🤖 {msg_body}"])
            target_ref = get_env("TARGET_REF") or get_env("GITHUB_HEAD_REF")
            if not target_ref:
                print("ℹ️  Not on a PR branch (no GITHUB_HEAD_REF). Skipping push.")
                sys.exit(0)
            print(f"🔄 Rebase and pushing to {target_ref}...")
            run(["git", "pull", "--rebase", "origin", target_ref], check=False)
            run(["git", "push", "origin", f"HEAD:{target_ref}"])

            if "GITHUB_OUTPUT" in os.environ:
                with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                    f.write("changes_pushed=true\n")
        except subprocess.CalledProcessError as e:
            print(f"❌ ::error::Failed to push fixes. ({e})")
            sys.exit(1)

        pr_num = get_env("PR_NUMBER")

        if not pr_num:
            try:
                pr_json = subprocess.check_output(
                    ["gh", "pr", "list", "--head", target_ref, "--json", "number"],
                    text=True,
                )
                prs = json.loads(pr_json)
                if prs:
                    pr_num = str(prs[0]["number"])
            except Exception:
                pass

        if pr_num:
            fixes_list = ""
            if fixed_format:
                fixes_list += "- ✅ **Formatted Code**\n"
            if fixed_version:
                fixes_list += f"- ✅ **Bumped Version** ({new_v})\n"

            body = f"### 🤖 Auto-Fix Applied\nI fixed the following issues so we can merge:\n{fixes_list}\n"
            body += f"**Note:** Build is now **GREEN** 🟢. Please run `git pull origin {target_ref}` locally.\n"

            Path("comment.md").write_text(body, encoding="utf-8")
            run(
                ["gh", "pr", "comment", pr_num, "--body-file", "comment.md"],
                check=False,
            )

        sys.exit(0)

    else:
        print("\n✨ Clean run. No changes needed.")
        if "GITHUB_OUTPUT" in os.environ:
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write("changes_pushed=false\n")


if __name__ == "__main__":
    main()
