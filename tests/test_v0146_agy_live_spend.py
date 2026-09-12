from advertpreneur_cli.provider_harness import ExternalProviderHarness
from advertpreneur_cli.cli import AdvertpreneurCLI


def test_agy_stream_write_tool_uses_nested_tool_info_and_target_file():
    row = {
        "event": "step_update",
        "step_update": {
            "step_index": 4,
            "state": "DONE",
            "step_type": "tool",
            "tool_name": "write_to_file",
            "tool_info": {
                "name": "write_to_file",
                "parameters": {
                    "TargetFile": r"D:\\Cli\\index.html",
                    "CodeContent": "<html></html>",
                },
                "output": "ok",
            },
        },
    }
    event = ExternalProviderHarness._activity_from_agy_row(row)
    assert event is not None
    assert event.kind == "tool"
    assert event.label == "Writing code · index.html"
    assert event.detail.endswith(r"Cli\\index.html")


def test_agy_stream_run_command_uses_commandline():
    row = {
        "event": "step_update",
        "step_update": {
            "step_index": 5,
            "state": "DONE",
            "step_type": "tool",
            "tool_name": "run_command",
            "tool_info": {"parameters": {"CommandLine": "pytest -q"}},
        },
    }
    event = ExternalProviderHarness._activity_from_agy_row(row)
    assert event is not None
    assert event.label == "Running tests"
    assert event.detail == "pytest -q"


def test_agy_user_input_step_is_not_fake_reasoning():
    row = {"event": "step_update", "step_update": {"step_type": "user_input", "state": "DONE"}}
    assert ExternalProviderHarness._activity_from_agy_row(row) is None


def test_agy_agent_response_is_truthful_progress_not_private_reasoning():
    row = {"event": "step_update", "step_update": {"step_type": "agent_response", "state": "ACTIVE", "text_delta": "Hi"}}
    event = ExternalProviderHarness._activity_from_agy_row(row)
    assert event is not None
    assert event.label == "Preparing response"
    assert "reason" not in event.label.lower()


def test_micro_coding_task_classifier_is_conservative():
    assert AdvertpreneurCLI._micro_coding_task("make a simple html file where the No button runs away")
    assert not AdvertpreneurCLI._micro_coding_task("fix the WordPress theme checkout authentication bug and run tests")


def test_agy_cache_is_additive_not_subset():
    from advertpreneur_cli.provider_harness import ProviderRun
    run = ProviderRun("agy", "gemini", "ok", 0, input_tokens=3401, cache_read_tokens=20317, cache_read_additive=True)
    assert run.uncached_input_tokens == 3401
    assert run.context_input_tokens == 23718
    assert 85.6 < run.cache_percent < 85.8



def test_external_live_status_is_compact_coding_file_elapsed():
    from pathlib import Path
    import advertpreneur_cli.tui as tui
    text = Path(tui.__file__).read_text(encoding="utf-8")
    assert 'working = f" {frame} coding{file_part} · {elapsed:.1f}s"' in text
