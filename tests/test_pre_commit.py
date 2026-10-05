import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def pre_commit():
    spec = importlib.util.spec_from_file_location(
        "pre_commit_script", Path(__file__).parents[1] / "pre-commit.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_ci_keeps_committed_version_without_git_or_file_access(
    pre_commit, tmp_path
):
    pre_commit.VERSION_FILE = tmp_path / "version.py"

    with (
        patch.dict(
            pre_commit.os.environ,
            {"CI": "true", "GITHUB_REF": "refs/heads/main"},
            clear=False,
        ),
        patch.object(pre_commit.subprocess, "run") as git_run,
        patch.object(pre_commit.subprocess, "check_output") as git_output,
    ):
        assert pre_commit.task_version_bump() == (False, "")

    git_run.assert_not_called()
    git_output.assert_not_called()
    assert not pre_commit.VERSION_FILE.exists()


@pytest.mark.parametrize(
    "environment",
    [{}, {"CI": "true", "GITHUB_REF": "refs/pull/12/merge"}],
)
def test_non_release_runs_bump_from_main_version(pre_commit, tmp_path, environment):
    version_file = tmp_path / "version.py"
    version_file.write_text('__version__ = "0.8.2"\n')
    pre_commit.VERSION_FILE = version_file
    completed = subprocess.CompletedProcess([], 0)

    with (
        patch.dict(pre_commit.os.environ, environment, clear=True),
        patch.object(pre_commit.subprocess, "run", return_value=completed) as git_run,
        patch.object(
            pre_commit.subprocess,
            "check_output",
            return_value='__version__ = "0.8.3"\n',
        ),
        patch.object(pre_commit, "run") as script_run,
    ):
        assert pre_commit.task_version_bump() == (True, "0.8.4")

    assert version_file.read_text() == '__version__ = "0.8.4"\n'
    assert git_run.called
    assert script_run.call_args_list[-1].args == (["git", "add", "."],)
