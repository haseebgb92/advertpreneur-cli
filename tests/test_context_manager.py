from advertpreneur_cli.context_manager import LocalContextManager


def test_local_compaction_preserves_paths_without_cloud():
    mgr = LocalContextManager(1000, 500)
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "Fix login credentials"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "write_file", "arguments": {"path": "src/auth/login.py", "content": "x" * 5000}}}]},
        {"role": "tool", "tool_name": "write_file", "content": "Created src/auth/login.py"},
        {"role": "assistant", "content": "Login fix completed in src/auth/login.py"},
    ]
    compacted, memory = mgr.compact_messages(messages, "system")
    assert "src/auth/login.py" in memory
    assert len(str(compacted)) < len(str(messages))
    assert all("tool_calls" not in m for m in compacted)
