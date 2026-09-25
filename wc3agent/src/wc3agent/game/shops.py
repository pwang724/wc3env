"""Item availability from installed stock timers and current tech; remaining stock is unobserved."""


def available_items(catalog, shop, now, tech):
    if shop.get("state") == "constructing" or shop["hp"] <= 0:
        return []
    return [
        {"type_id": raw, **catalog.items[raw]}
        for raw in catalog.units[shop["type_id"]]["sells_items"]
        if raw in catalog.items
        and now >= catalog.items[raw]["stock_start_seconds"]
        and not catalog.missing(catalog.items[raw].get("requires", []), tech)
    ]
