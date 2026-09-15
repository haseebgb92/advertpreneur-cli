from advertpreneur_cli.xray_workflow import XrayProgress, next_xray_action


def test_more_rows_keeps_loading():
    assert next_xray_action(XrayProgress(20, False), 40, True) == "load_more"


def test_stall_refreshes_once_then_records_no_data():
    assert next_xray_action(XrayProgress(20, False), 20, True) == "refresh"
    assert next_xray_action(XrayProgress(20, True), 20, True) == "no_data"


def test_hidden_load_more_exports_populated_rows():
    assert next_xray_action(XrayProgress(20, False), 20, False) == "export"
