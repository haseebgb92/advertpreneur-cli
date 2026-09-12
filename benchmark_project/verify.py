from taskboard import add_task, delete_task, filter_tasks, summarize
from taskboard.models import normalize_priority


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    tasks = []
    a = add_task(tasks, "Ship release", "HIGH")
    b = add_task(tasks, "Write docs", "low")
    c = add_task(tasks, "Run tests", "medium")

    check([a["id"], b["id"], c["id"]] == [1, 2, 3], "initial IDs should be sequential")
    check(normalize_priority("nonsense") == "medium", "invalid priority should fall back to medium")

    check(delete_task(tasks, 2), "delete existing task should return true")
    d = add_task(tasks, "Publish notes", "low")
    check(d["id"] == 4, "new IDs must never reuse deleted IDs")

    a["completed"] = True
    d["completed"] = True

    ordered = filter_tasks(tasks)
    check([x["priority"] for x in ordered] == ["high", "medium", "low"], "tasks should sort high -> medium -> low")

    done = filter_tasks(tasks, "true")
    pending = filter_tasks(tasks, "false")
    check({x["id"] for x in done} == {1, 4}, "string true filter should return completed tasks")
    check({x["id"] for x in pending} == {3}, "string false filter should return incomplete tasks")

    report = summarize(tasks)
    check(report == {
        "total": 3,
        "completed": 2,
        "pending": 1,
        "completion_percent": 66.67,
    }, f"summary mismatch: {report}")

    print("PASS: TaskBoard multi-file repair benchmark")


if __name__ == "__main__":
    main()
