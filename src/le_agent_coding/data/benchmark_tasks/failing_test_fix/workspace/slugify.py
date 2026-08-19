def slugify(value: str) -> str:
    return "-".join(value.strip().lower().split(" "))
