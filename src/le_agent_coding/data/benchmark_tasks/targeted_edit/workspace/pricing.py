def shipping_fee(total: float, *, member: bool = False) -> int:
    if member:
        return 0
    return 10 if total < 50 else 0
