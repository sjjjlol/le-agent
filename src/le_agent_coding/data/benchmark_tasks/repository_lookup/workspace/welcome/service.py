from welcome.render import format_salutation


def build_welcome(name: str) -> str:
    return format_salutation(name)
