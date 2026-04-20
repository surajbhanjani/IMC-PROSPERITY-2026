from datamodel import OrderDepth, TradingState, Order
from typing import List
import jsonpickle

class Trader:

    def run(self, state: TradingState):
        result = {}

        # Initialize state memory for BOTH assets now
        traderData = jsonpickle.decode(state.traderData) if state.traderData else {
            "OSMIUM_EMA": None, 
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

            total_bid_vol = sum(order_depth.buy_orders.values())
            total_ask_vol = sum(abs(v) for v in order_depth.sell_orders.values())

            position = state.position.get(product, 0)
            
            # Universal VWAP Microprice (Sees through the whole book)
            bid_vwap = sum(p * v for p, v in order_depth.buy_orders.items()) / total_bid_vol if total_bid_vol > 0 else best_bid
            ask_vwap = sum(p * abs(v) for p, v in order_depth.sell_orders.items()) / total_ask_vol if total_ask_vol > 0 else best_ask
            
            microprice = (bid_vwap * total_ask_vol + ask_vwap * total_bid_vol) / (total_bid_vol + total_ask_vol) if (total_bid_vol + total_ask_vol) > 0 else (best_bid + best_ask) / 2
            imbalance = total_bid_vol / (total_bid_vol + total_ask_vol)

            # =========================
            # 🌑 ASH_COATED_OSMIUM (Slow Mean-Reversion)
            # =========================
            if product == "ASH_COATED_OSMIUM":
                LIMIT = 20

                # 1. Slow EMA (Alpha 0.05) to track the drifting mean
                if traderData["OSMIUM_EMA"] is None:
                    traderData["OSMIUM_EMA"] = microprice
                else:
                    traderData["OSMIUM_EMA"] = 0.05 * microprice + 0.95 * traderData["OSMIUM_EMA"]

                base_fair = traderData["OSMIUM_EMA"]
                
                # 2. High Risk Aversion (0.8) - Forces inventory back to 0 quickly
                reservation_price = base_fair - (position * 0.8)
                reservation_price += (imbalance - 0.5) * 1

                buy_qty  =  LIMIT - position
                sell_qty = -LIMIT - position

                # 3. Dynamic Pennying
                buy_price  = min(best_bid + 1, int(round(reservation_price - 1)))
                sell_price = max(best_ask - 1, int(round(reservation_price + 1)))

                # 4. Tranching (Staggered Execution)
                if buy_qty > 0:
                    l1 = buy_qty // 2
                    l2 = buy_qty - l1
                    if l1 > 0: orders.append(Order(product, buy_price, l1))
                    if l2 > 0: orders.append(Order(product, buy_price - 1, l2))

                if sell_qty < 0:
                    l1 = sell_qty // 2
                    l2 = sell_qty - l1
                    if l1 < 0: orders.append(Order(product, sell_price, l1))
                    if l2 < 0: orders.append(Order(product, sell_price + 1, l2))

            # =========================
            # 🌶️ INTARIAN_PEPPER_ROOT (Aggressive Uptrend)
            # =========================
            elif product == "INTARIAN_PEPPER_ROOT":
                LIMIT = 20

                # 1. Fast EMA (Alpha 0.3) to track the aggressive 1000-tick daily trend
                if traderData["PEPPER_EMA"] is None:
                    traderData["PEPPER_EMA"] = microprice
                else:
                    traderData["PEPPER_EMA"] = 0.3 * microprice + 0.7 * traderData["PEPPER_EMA"]

                base_fair = traderData["PEPPER_EMA"]

                # 2. Lower Risk Aversion (0.5) - Allows the bot to ride the trend longer
                reservation_price = base_fair - (position * 0.5)
                reservation_price += (imbalance - 0.5) * 1

                buy_qty  =  LIMIT - position
                sell_qty = -LIMIT - position

                # 3. Dynamic Pennying
                buy_price  = min(best_bid + 1, int(round(reservation_price - 1)))
                sell_price = max(best_ask - 1, int(round(reservation_price + 1)))

                # 4. Tranching (Staggered Execution)
                if buy_qty > 0:
                    l1 = buy_qty // 2
                    l2 = buy_qty - l1
                    if l1 > 0: orders.append(Order(product, buy_price, l1))
                    if l2 > 0: orders.append(Order(product, buy_price - 1, l2))

                if sell_qty < 0:
                    l1 = sell_qty // 2
                    l2 = sell_qty - l1
                    if l1 < 0: orders.append(Order(product, sell_price, l1))
                    if l2 < 0: orders.append(Order(product, sell_price + 1, l2))

            result[product] = orders

        encoded_data = jsonpickle.encode(traderData)
        return result, 0, encoded_data