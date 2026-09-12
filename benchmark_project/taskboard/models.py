PRIORITY_ORDER = {"low": 0, "medium": 1, "high": 2}


def normalize_priority(value: str) -> str:
    value = (value or "medium").strip().lower()
    if value not in PRIORITY_ORDER:
        return "low"  # BUG: invalid/missing priorities should fall back consistently
    return value


def sort_key(task: dict):
    # BUG: alphabetical priority ordering is not the product requirement.
    return (task.get("priority", "medium"), task.get("id", 0))
