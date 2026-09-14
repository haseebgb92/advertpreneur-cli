from pathlib import Path
from advertpreneur_cli.self_healing import SelfHealingEngine, FailureTrace


def test_self_healing_diagnose_pytest_failure(tmp_path: Path) -> None:
    engine = SelfHealingEngine(tmp_path)
    sample_output = """
============================= test session starts =============================
tests/test_sample.py::test_calculation FAILED

______________________________ test_calculation _______________________________

    def test_calculation():
>       assert 2 + 2 == 5
E       assert 4 == 5

tests/test_sample.py:4: AssertionError
=========================== 1 failed in 0.05s ===========================
"""
    trace = engine.diagnose("pytest tests/test_sample.py", sample_output)
    assert trace is not None
    assert trace.error_type == "TestAssertionError"
    assert "test_sample.py::test_calculation" in trace.failed_target
    assert "assert 4 == 5" in trace.traceback

    prompt = engine.repair_prompt(trace, attempt=1)
    assert "Self-Healing Repair Attempt 1/3" in prompt
    assert "TestAssertionError" in prompt


def test_self_healing_diagnose_syntax_error(tmp_path: Path) -> None:
    engine = SelfHealingEngine(tmp_path)
    sample_output = """
Traceback (most recent call last):
  File "main.py", line 12
    def invalid syntax
        ^^^^^^
SyntaxError: invalid syntax
"""
    trace = engine.diagnose("python main.py", sample_output)
    assert trace is not None
    assert trace.error_type == "PythonRuntimeError"
    assert "SyntaxError: invalid syntax" in trace.summary


def test_self_healing_empty_output(tmp_path: Path) -> None:
    engine = SelfHealingEngine(tmp_path)
    trace = engine.diagnose("pytest", "")
    assert trace is None
