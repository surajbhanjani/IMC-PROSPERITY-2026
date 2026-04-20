from datamodel import OrderDepth, TradingState, Order
from typing import List
import jsonpickle

class Trader:

    def run(self, state: TradingState):
        result = {}

        data = jsonpickle.decode(state.traderData) if state.traderData else {}

        for product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []

            if not order_depth.buy_orders or not order_depth.sell_orders:
                result[product] = orders
                continue

            best_bid = max(order_depth.buy_orders.keys())
            best_ask = min(order_depth.sell_orders.keys())

            bid_vol = order_depth.buy_orders[best_bid]
            ask_vol = abs(order_depth.sell_orders[best_ask])

            position = state.position.get(product, 0)
            LIMIT = 20

            buy_cap = LIMIT - position
            sell_cap = LIMIT + position

            mid = (best_bid + best_ask) / 2
            spread = best_ask - best_bid

            # ============================================================
            # 🔥 TREND PRODUCT (AGGRESSIVE)
            # ============================================================
            if "PEPPER" in product or "TREND" in product:

                # FULL SWEEP EARLY
                if state.timestamp < 2000:
                    for ask in sorted(order_depth.sell_orders.keys()):
                        if buy_cap <= 0:
                            break
                        vol = abs(order_depth.sell_orders[ask])
                        qty = min(vol, buy_cap)
                        orders.append(Order(product, ask, qty))
                        buy_cap -= qty

                # CONTINUOUS REFILL
                for ask in sorted(order_depth.sell_orders.keys()):
                    if buy_cap <= 0:
                        break
                    qty = min(abs(order_depth.sell_orders[ask]), buy_cap)
                    orders.append(Order(product, ask, qty))
                    buy_cap -= qty

            # ============================================================
            # 💎 MEAN REVERSION + MM
            # ============================================================
            else:

                # IMBALANCE
                imbalance = bid_vol / (bid_vol + ask_vol)

                fair = mid - position * 1.5

                # AGGRESSIVE EDGE
                if imbalance > 0.6 and buy_cap > 0:
                    qty = min(ask_vol, buy_cap)
                    orders.append(Order(product, best_ask, qty))

                if imbalance < 0.4 and sell_cap > 0:
                    qty = min(bid_vol, sell_cap)
                    orders.append(Order(product, best_bid, -qty))

                # LAYERED MM
                if buy_cap > 0:
                    orders.append(Order(product, best_bid + 1, min(10, buy_cap)))
                    orders.append(Order(product, best_bid, min(5, buy_cap)))

                if sell_cap > 0:
                    orders.append(Order(product, best_ask - 1, -min(10, sell_cap)))
                    orders.append(Order(product, best_ask, -min(5, sell_cap)))

            # ============================================================
            # 🛑 RISK CONTROL
            # ============================================================
            if abs(position) > LIMIT * 0.9:
                if position > 0:
                    orders.append(Order(product, best_bid, -min(5, position)))
                else:
                    orders.append(Order(product, best_ask, min(5, -position)))

            result[product] = orders

        return result, 0, jsonpickle.encode(data)