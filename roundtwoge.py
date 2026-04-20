from datamodel import OrderDepth, TradingState, Order
from typing import List
import jsonpickle

class Trader:

    def run(self, state: TradingState):
        result = {}

        # ── Persistent State ───────────────────────────────────────────────
        traderData = jsonpickle.decode(state.traderData) if state.traderData else {
            "osmium_ema":      10000.0,
            "pepper_day_base": None,
            "prev_timestamp":  -1,
            "pepper_history":  [] # NEW: Tracks rolling Pepper prices
        }

        timestamp = state.timestamp

        # ── Day rollover (timestamp resets to 0 each new day) ─────────────
        if timestamp < traderData["prev_timestamp"]:
            traderData["pepper_day_base"] = None
            traderData["osmium_ema"]      = 10000.0
            traderData["pepper_history"]  = [] # Reset history on new day
        traderData["prev_timestamp"] = timestamp

        # ==============================================================
        # 📡 CROSS-ASSET SIGNAL GENERATION (PEPPER -> OSMIUM)
        # ==============================================================
        pepper_mid = None
        if "INTARIAN_PEPPER_ROOT" in state.order_depths:
            p_depth = state.order_depths["INTARIAN_PEPPER_ROOT"]
            p_bid = max(p_depth.buy_orders.keys()) if p_depth.buy_orders else None
            p_ask = min(p_depth.sell_orders.keys()) if p_depth.sell_orders else None
            
            if p_bid and p_ask: pepper_mid = (p_bid + p_ask) / 2.0
            elif p_ask: pepper_mid = float(p_ask)
            elif p_bid: pepper_mid = float(p_bid)

        # Update rolling history (Keep last 4: Current + 3 previous timestamps)
        if pepper_mid is not None:
            traderData["pepper_history"].append(pepper_mid)
            if len(traderData["pepper_history"]) > 4:
                traderData["pepper_history"].pop(0)

        # Check for sharp drop: > 15 ticks over 3 timestamps
        osmium_spike_expected = False
        if len(traderData["pepper_history"]) == 4:
            past_price = traderData["pepper_history"][0]
            current_price = traderData["pepper_history"][-1]
            if past_price - current_price > 15:
                osmium_spike_expected = True

        for product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []

            best_bid = max(order_depth.buy_orders.keys())  if order_depth.buy_orders  else None
            best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None

            if best_bid is None and best_ask is None:
                result[product] = orders
                continue

            position = state.position.get(product, 0)
            LIMIT    = 20
            buy_cap  = LIMIT - position
            sell_cap = LIMIT + position

            # ==============================================================
            # 🌿  INTARIAN_PEPPER_ROOT  —  TREND RIDING
            # ==============================================================
            if product == "INTARIAN_PEPPER_ROOT":

                mid = pepper_mid if pepper_mid else 10000 # Fallback

                if traderData["pepper_day_base"] is None or timestamp < 5000:
                    estimated = mid - timestamp * 0.001
                    snapped   = round(estimated / 1000.0) * 1000
                    traderData["pepper_day_base"] = float(snapped)

                day_base = traderData["pepper_day_base"]
                fair     = day_base + timestamp * 0.001

                # ── TIER 1: ask strictly below fair ─
                if best_ask is not None and best_ask < fair and buy_cap > 0:
                    ask_vol = abs(order_depth.sell_orders[best_ask])
                    qty     = min(ask_vol, buy_cap, LIMIT)
                    orders.append(Order(product, best_ask, qty))
                    buy_cap -= qty

                # ── TIER 2: buy at ask <= fair+12 ─
                if buy_cap > 0 and best_ask is not None:
                    for ask_px in sorted(order_depth.sell_orders.keys()):
                        if ask_px <= fair + 12 and buy_cap > 0:
                            qty = min(abs(order_depth.sell_orders[ask_px]), buy_cap, LIMIT)
                            orders.append(Order(product, ask_px, qty))
                            buy_cap -= qty
                        elif ask_px > fair + 12:
                            break

                # ── TIER 3: Defensive sell ───────────
                if best_bid is not None and sell_cap > 0 and position > 10:
                    if best_bid >= fair + 12:
                        bid_vol = order_depth.buy_orders[best_bid]
                        qty     = min(bid_vol, sell_cap, 3)
                        orders.append(Order(product, best_bid, -qty))

            # ==============================================================
            # 💎  ASH_COATED_OSMIUM  —  MEAN REVERSION & DIVERGENCE ARBITRAGE
            # ==============================================================
            elif product == "ASH_COATED_OSMIUM":

                if best_bid and best_ask:
                    mid = (best_bid + best_ask) / 2.0
                elif best_ask:
                    mid = float(best_ask)
                else:
                    mid = float(best_bid)

                ALPHA = 0.015
                ema   = (1.0 - ALPHA) * traderData["osmium_ema"] + ALPHA * mid
                traderData["osmium_ema"] = ema

                # ── OVERRIDE: PEPPER DIVERGENCE SIGNAL FIRED ─────────────────
                # If Pepper dropped 15 ticks, OSMIUM is about to spike. 
                # Ignore the strict 9998 rule and buy aggressively up to 10002.
                if osmium_spike_expected and best_ask is not None and buy_cap > 0:
                    for ask_px in sorted(order_depth.sell_orders.keys()):
                        if ask_px <= 10002 and buy_cap > 0:
                            qty = min(abs(order_depth.sell_orders[ask_px]), buy_cap, 10) # Grab up to 10 units fast
                            orders.append(Order(product, ask_px, qty))
                            buy_cap -= qty
                        elif ask_px > 10002:
                            break

                # ── NORMAL BUY: ask <= 9998 AND bid >= 9990 ──────────────────
                if best_ask is not None and best_bid is not None and buy_cap > 0:
                    if best_ask <= 9998 and best_bid >= 9990:
                        ask_vol = abs(order_depth.sell_orders[best_ask])
                        qty     = min(ask_vol, buy_cap, 5)
                        orders.append(Order(product, best_ask, qty))
                        buy_cap -= qty

                        for ask_px in sorted(order_depth.sell_orders.keys()):
                            if ask_px <= 9998 and buy_cap > 0:
                                qty = min(abs(order_depth.sell_orders[ask_px]), buy_cap, 5)
                                orders.append(Order(product, ask_px, qty))
                                buy_cap -= qty
                            elif ask_px > 9998:
                                break

                # ── NORMAL SELL: bid >= 10001 AND we are long ────────────────
                if best_bid is not None and sell_cap > 0 and position > 0:
                    if best_bid >= 10001:
                        bid_vol = order_depth.buy_orders[best_bid]
                        qty     = min(bid_vol, min(sell_cap, position), 5)
                        orders.append(Order(product, best_bid, -qty))
                        sell_cap -= qty

                        for bid_px in sorted(order_depth.buy_orders.keys(), reverse=True):
                            if bid_px >= 10001 and sell_cap > 0 and position > 0:
                                qty = min(order_depth.buy_orders[bid_px], sell_cap, 5)
                                orders.append(Order(product, bid_px, -qty))
                                sell_cap -= qty
                            elif bid_px < 10001:
                                break

                # ── INVENTORY BLEED: prevent accumulation ─────────────────
                if position > LIMIT - 3 and best_bid is not None:
                    qty = min(2, position - (LIMIT - 4))
                    if qty > 0:
                        orders.append(Order(product, best_bid, -qty))

                if position < 0 and best_ask is not None:
                    qty = min(3, -position)
                    orders.append(Order(product, best_ask, qty))

            result[product] = orders

        traderData = jsonpickle.encode(traderData)
        return result, 0, traderData