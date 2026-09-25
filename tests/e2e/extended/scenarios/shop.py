"""Buying from neutral buildings: items from a shop, mercenaries from a camp, a hero from a tavern."""

from __future__ import annotations

from tests.e2e.scenarios.base import ScenarioAgent, Step, View
from wc3env.protocol import Action


class Shop(ScenarioAgent):
    def __init__(self, shop: str, buyer: str, item: str, sells: str = "item", costs: bool = True):
        """`sells` is "item" (carried by the buyer, reported by item_sold) or "unit" (a new own unit of that type).

        Neutral stock appears after a delay, so the order repeats until the purchase lands.
        """
        super().__init__()
        start = {}

        def customer(v: View):
            return v.first(buyer)

        def buy(v: View):
            start.setdefault("gold", v.gold)
            start.setdefault("owned", len(v.own(item)))
            return [
                Action(customer(v)["unit_id"], "buy", {"shop_id": v.visible(shop)[0]["unit_id"], "item_type_id": item})
            ]

        def bought(v: View) -> bool:
            if costs and v.gold >= start["gold"]:
                return False
            if sells == "unit":
                return len(v.own(item)) > start["owned"]
            carried = [s["type_id"] for s in v.p["inventory"] if s["unit_id"] == customer(v)["unit_id"]]
            return v.happened("item_sold", type_id=item) and item in carried

        self.steps = [
            Step("staged", lambda v: [], lambda v: bool(customer(v) and v.visible(shop)), timeout=5),
            Step("buy", buy, bought, timeout=400, every=10),
        ]
