from .models import normalize_priority, sort_key


def add_task(tasks: list[dict], title: str, priority: str = "medium") -> dict:
    # BUG: len()+1 can reuse an ID after a task is deleted.
    task = {
        "id": len(tasks) + 1,
        "title": title.strip(),
        "priority": normalize_priority(priority),
        "completed": False,
    }
    tasks.append(task)
    return task


def delete_task(tasks: list[dict], task_id: int) -> bool:
    for i, task in enumerate(tasks):
        if task["id"] == task_id:
            tasks.pop(i)
            return True
    return False


def filter_tasks(tasks: list[dict], completed=None) -> list[dict]:
    result = list(tasks)
    if completed is not None:
        # BUG: strings like "false" are truthy and produce the wrong filter.
        result = [task for task in result if task.get("completed") is bool(completed)]
    return sorted(result, key=sort_key)
