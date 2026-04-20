from datamodel import OrderDepth, TradingState, Order
from typing import List
import jsonpickle

class Trader:

    def run(self, state: TradingState):
        result = {}

        traderData = jsonpickle.decode(state.traderData) if state.traderData else {
            "PEPPER_EMA": None
        }

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

            mid = (best_bid + best_ask) / 2
            spread = best_ask - best_bid

            # ============================================================
            # 💎 ASH-COATED OSMIUM (STABLE MONEY ENGINE)
            # ============================================================
            if product == "ASH_COATED_OSMIUM":

                fair = mid - position * 1.2

                buy_qty = LIMIT - position
                sell_qty = -LIMIT - position

                # 🔥 Aggressive only if clear edge
                if best_ask < fair - 1 and buy_qty > 0:
                    vol = min(5, buy_qty)
                    orders.append(Order(product, best_ask, vol))
                    buy_qty -= vol

                if best_bid > fair + 1 and sell_qty < 0:
                    vol = min(5, abs(sell_qty))
                    orders.append(Order(product, best_bid, -vol))
                    sell_qty += vol

                # 💰 Core Market Making (MAIN PROFIT)
                if buy_qty > 0:
                    orders.append(Order(product, best_bid + 1, min(10, buy_qty)))

                if sell_qty < 0:
                    orders.append(Order(product, best_ask - 1, max(-10, sell_qty)))

            # ============================================================
            # 🌿 INTARIAN PEPPER ROOT (FIXED PROFIT ENGINE)
            # ============================================================
            elif product == "INTARIAN_PEPPER_ROOT":

                # Microprice
                micro = (best_bid * ask_vol + best_ask * bid_vol) / (bid_vol + ask_vol)

                # EMA
                if traderData["PEPPER_EMA"] is None:
                    traderData["PEPPER_EMA"] = micro
                else:
                    traderData["PEPPER_EMA"] = 0.25 * micro + 0.75 * traderData["PEPPER_EMA"]

                ema = traderData["PEPPER_EMA"]

                diff = micro - ema

                # 🔥 Strong inventory control
                fair = ema - position * 1.2

                buy_qty = LIMIT - position
                sell_qty = -LIMIT - position

                EDGE = 1.5

                # ❗ ONLY TRADE WHEN REAL EDGE EXISTS
                if spread >= 2:

                    # Directional trades (FIXED)
                    if diff < -EDGE and buy_qty > 0:
                        vol = min(2, buy_qty)
                        orders.append(Order(product, best_ask, vol))
                        buy_qty -= vol

                    if diff > EDGE and sell_qty < 0:
                        vol = min(2, abs(sell_qty))
                        orders.append(Order(product, best_bid, -vol))
                        sell_qty += vol

                    # 💰 Passive MM (MAIN ENGINE)
                    if buy_qty > 0:
                        orders.append(Order(product, best_bid + 1, min(6, buy_qty)))

                    if sell_qty < 0:
                        orders.append(Order(product, best_ask - 1, max(-6, sell_qty)))

            # ============================================================
            # 🛑 SAFETY EXIT (CRITICAL)
            # ============================================================
            if abs(position) > LIMIT * 0.85:
                if position > 0:
                    orders.append(Order(product, best_bid, -min(5, position)))
                else:
                    orders.append(Order(product, best_ask, min(5, -position)))

            result[product] = orders

        traderData = jsonpickle.encode(traderData)
        return result, 0, traderData