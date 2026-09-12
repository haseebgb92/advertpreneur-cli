def summarize(tasks: list[dict]) -> dict:
    completed = sum(1 for task in tasks if task.get("completed"))
    # BUG: pending and completion percentage are calculated incorrectly.
    pending = len(tasks) - completed - 1
    percent = completed / len(tasks) if tasks else 0
    return {
        "total": len(tasks),
        "completed": completed,
        "pending": pending,
        "completion_percent": percent,
    }
