"""Tests for the CodeAct Python tool and its safety guard."""

import os
import types

import agent
import tools


def test_execute_python_runs_a_safe_script():
    out = tools.fn_execute_python(
        "import statistics\nprint(statistics.mean([2, 4, 6]))"
    )
    assert "4" in out


def test_execute_python_returns_errors():
    out = tools.fn_execute_python('raise ValueError("boom")')
    assert "ValueError" in out
    assert "boom" in out


def test_execute_python_blocks_os_system():
    out = tools.fn_execute_python("import os\nos.system('rm -rf /')")
    assert "Refused" in out
    assert "os" in out


def test_execute_python_blocks_subprocess():
    out = tools.fn_execute_python("import subprocess\nsubprocess.run(['ls'])")
    assert "Refused" in out
    assert "subprocess" in out


def test_execute_python_blocks_shutil_rmtree():
    out = tools.fn_execute_python("from shutil import rmtree\nrmtree('/')")
    assert "Refused" in out
    assert "shutil" in out


def test_execute_python_blocks_sys_exit():
    out = tools.fn_execute_python("import sys\nsys.exit(1)")
    assert "Refused" in out
    assert "sys" in out


def test_execute_python_blocks_dynamic_execution():
    assert "Refused" in tools.fn_execute_python("eval('1 + 1')")
    assert "Refused" in tools.fn_execute_python("__import__('os')")
    assert "Refused" in tools.fn_execute_python("globals()")
    assert "Refused" in tools.fn_execute_python("getattr(object, 'x')")


def test_execute_python_timeout_is_reported(monkeypatch):
    def boom(*_args, **_kwargs):
        raise tools.subprocess.TimeoutExpired(cmd="python", timeout=15)

    monkeypatch.setattr(tools.subprocess, "run", boom)
    assert "timed out" in tools.fn_execute_python("print('slow')").lower()


def test_execute_python_removes_the_temp_file(monkeypatch):
    seen = {}

    def fake_run(args, **kwargs):
        seen["path"] = args[1]
        assert os.path.exists(seen["path"])
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    assert tools.fn_execute_python("print('ok')") == "ok"
    assert not os.path.exists(seen["path"])


def test_execute_python_caps_large_output(monkeypatch):
    def fake_run(_args, **_kwargs):
        return types.SimpleNamespace(returncode=0, stdout="x" * 5000, stderr="")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    out = tools.fn_execute_python("print('ok')")
    assert out.endswith("...[truncated]")
    assert len(out) < 5000


def test_execute_python_allows_the_main_guard():
    out = tools.fn_execute_python(
        "if __name__ == '__main__':\n    print('main guard')\n"
    )
    assert "main guard" in out


def test_execute_python_is_registered_and_documented():
    names = {fn.__name__ for fn in agent._TOOLS}
    assert "fn_execute_python" in names
    assert "fn_execute_python" in agent.SYSTEM_INSTRUCTION
    assert "safe, standard Python libraries" in agent.SYSTEM_INSTRUCTION


def test_codeact_queries_receive_the_python_tool():
    assert agent.classify_intent(
        "write a python script to calculate prime numbers"
    ) == "CODE"
    schemas = agent.tools_for("write a python script to calculate primes")
    names = {schema["function"]["name"] for schema in schemas}
    assert "fn_execute_python" in names
