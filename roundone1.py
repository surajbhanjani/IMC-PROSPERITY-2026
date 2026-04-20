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

            total_bid_vol = sum(order_depth.buy_orders.values())
            total_ask_vol = sum(abs(v) for v in order_depth.sell_orders.values())

            position = state.position.get(product, 0)
            LIMIT = 20

            # ============================================================
            # 💎 ASH-COATED OSMIUM (PURE MARKET MAKING)
            # ============================================================
            if product == "ASH_COATED_OSMIUM":

                FAIR = 10000

                buy_qty = LIMIT - position
                sell_qty = -LIMIT - position

                # -------------------------------
                # 🔥 AGGRESSIVE SWEEP (FREE EDGE)
                # -------------------------------
                for ask_price in sorted(order_depth.sell_orders.keys()):
                    if ask_price <= FAIR and buy_qty > 0:
                        vol = min(abs(order_depth.sell_orders[ask_price]), buy_qty)
                        orders.append(Order(product, ask_price, vol))
                        buy_qty -= vol

                for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                    if bid_price >= FAIR and sell_qty < 0:
                        vol = max(-order_depth.buy_orders[bid_price], sell_qty)
                        orders.append(Order(product, bid_price, vol))
                        sell_qty -= vol

                # -------------------------------
                # 🛡️ INVENTORY SKEW
                # -------------------------------
                skew = position * 1
                fair_adj = FAIR - skew

                # -------------------------------
                # 💰 PASSIVE MM
                # -------------------------------
                if buy_qty > 0:
                    buy_price = min(best_bid + 1, fair_adj)
                    orders.append(Order(product, buy_price, buy_qty))

                if sell_qty < 0:
                    sell_price = max(best_ask - 1, fair_adj)
                    orders.append(Order(product, sell_price, sell_qty))

            # ============================================================
            # 🌿 INTARIAN PEPPER ROOT (MEAN REVERSION + MICROPRICE)
            # ============================================================
            elif product == "INTARIAN_PEPPER_ROOT":

                # -------------------------------
                # 🔍 MICROPRICE
                # -------------------------------
                microprice = (best_bid * ask_vol + best_ask * bid_vol) / (bid_vol + ask_vol)

                # -------------------------------
                # 📈 EMA (FAIR VALUE)
                # -------------------------------
                if traderData["PEPPER_EMA"] is None:
                    traderData["PEPPER_EMA"] = microprice
                else:
                    traderData["PEPPER_EMA"] = 0.3 * microprice + 0.7 * traderData["PEPPER_EMA"]

                fair = traderData["PEPPER_EMA"]

                # -------------------------------
                # ⚖️ IMBALANCE (LIGHT SIGNAL)
                # -------------------------------
                imbalance = total_bid_vol / (total_bid_vol + total_ask_vol)
                fair += (imbalance - 0.5) * 1

                # -------------------------------
                # 🛡️ INVENTORY CONTROL
                # -------------------------------
                fair -= position * 0.4

                buy_qty = LIMIT - position
                sell_qty = -LIMIT - position

                # -------------------------------
                # ⚡ AGGRESSIVE EDGE
                # -------------------------------
                if best_ask < fair and buy_qty > 0:
                    vol = min(5, buy_qty)
                    orders.append(Order(product, best_ask, vol))
                    buy_qty -= vol

                if best_bid > fair and sell_qty < 0:
                    vol = min(5, abs(sell_qty))
                    orders.append(Order(product, best_bid, -vol))
                    sell_qty += vol

                # -------------------------------
                # 💰 PASSIVE MM
                # -------------------------------
                buy_price = min(best_bid + 1, int(round(fair - 1)))
                sell_price = max(best_ask - 1, int(round(fair + 1)))

                if buy_qty > 0:
                    orders.append(Order(product, buy_price, buy_qty))

                if sell_qty < 0:
                    orders.append(Order(product, sell_price, sell_qty))

            # ============================================================
            # 🛑 SAFETY EXIT (ALL PRODUCTS)
            # ============================================================
            if abs(position) > LIMIT * 0.9:
                if position > 0:
                    orders.append(Order(product, best_bid, -min(5, position)))
                else:
                    orders.append(Order(product, best_ask, min(5, -position)))

            result[product] = orders

        traderData = jsonpickle.encode(traderData)
        return result, 0, traderData