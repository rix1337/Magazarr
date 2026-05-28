## Pre-commit hook

Run `pre-commit.py` before committing to catch lint errors and ensure the version is bumped vs main:

```bash
echo 'uv run pre-commit.py' > .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

Or run manually:

```bash
uv run pre-commit.py
```

CI runs it automatically with `--ci` which auto-fixes and pushes any changes.
