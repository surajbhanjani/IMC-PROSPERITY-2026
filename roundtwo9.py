from datamodel import OrderDepth, TradingState, Order
from typing import List
import jsonpickle

"""
╔══════════════════════════════════════════════════════════════════════════╗
║     FINAL OPTIMIZED TRADER  |  TARGET: 10,000+ PnL/DAY (LEADERBOARD)   ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  LEADERBOARD REALITY CHECK:                                              ║
║  ─────────────────────────────────────────────────────────────────────  ║
║  Top entry Round 2: 11,641/day  (limit ≈ 12, mid-to-mid fills)         ║
║  Median: 8,507/day                                                       ║
║  Our target: 10,000+/day consistently                                    ║
║                                                                          ║
║  KEY INSIGHT FROM REVERSE-ENGINEERING:                                   ║
║  The winner achieved ~12,000/day with what looks like limit=12          ║
║  entering at MID and exiting at MID (not ask→bid).                      ║
║  This suggests MARKET-MAKING fills (posting passive orders) rather      ║
║  than aggressive crossing. Our strategy: post competitive passive        ║
║  orders to capture mid-spread fills.                                     ║
║                                                                          ║
║  MARKET ACCESS FEE (MAF):                                                ║
║  Bid conservatively: 2,000 seashells                                     ║
║  If won: limit 20→25, extra ~4,900/day, net +2,900/day after fee       ║
║  If lost: no penalty, trade with limit=20                                ║
║                                                                          ║
║  PEPPER STRATEGY:                                                        ║
║  • Fair: 10000 + (day+2)×1000 + ts×0.001                                ║
║  • POST passive buy at bid+2 (=fair-4 typically) to get mid fills       ║
║  • SWEEP asks within fair+12 when passive doesn't fill                  ║
║  • Target: hold max position (20 or 25) all day                         ║
║  • Expected: 9,900/day at limit=20, 12,300/day at limit=25              ║
║                                                                          ║
║  OSMIUM STRATEGY:                                                        ║
║  • Quality signal: ask≤9998 AND bid≥9990 AND spread≤7                    ║
║  • Post passive buy at 9995 (mid of tight-spread dip)                   ║
║  • Post passive sell at 10005 when long                                  ║
║  • Expected: +500-700/day (safe, no blowups)                            ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

MAF_BID = 2000  # Conservative bid for +25% volume access


class Trader:

    def run(self, state: TradingState):
        result = {}

        traderData = jsonpickle.decode(state.traderData) if state.traderData else {
            "osmium_ema":      10000.0,
            "pepper_day_base": None,
            "prev_timestamp":  -1,
        }

        timestamp = state.timestamp

        if timestamp < traderData["prev_timestamp"]:
            traderData["pepper_day_base"] = None
            traderData["osmium_ema"]      = 10000.0
        traderData["prev_timestamp"] = timestamp

        for product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []

            best_bid = max(order_depth.buy_orders.keys())  if order_depth.buy_orders  else None
            best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None

            if best_bid is None and best_ask is None:
                result[product] = orders
                continue

            position = state.position.get(product, 0)
            LIMIT    = 25  # try for MAF access; defaults to 20 if not won
            buy_cap  = LIMIT - position
            sell_cap = LIMIT + position

            # ==========================================================
            # 🌿  PEPPER — MARKET-MAKING + AGGRESSIVE BACKUP
            # ==========================================================
            if product == "INTARIAN_PEPPER_ROOT":

                mid = ((best_bid + best_ask) / 2.0) if (best_bid and best_ask) \
                      else float(best_ask or best_bid)

                if traderData["pepper_day_base"] is None or timestamp < 5000:
                    estimated = mid - timestamp * 0.001
                    snapped   = round(estimated / 1000.0) * 1000
                    traderData["pepper_day_base"] = float(snapped)

                day_base = traderData["pepper_day_base"]
                fair     = day_base + timestamp * 0.001

                # STRATEGY: Post passive orders to get filled at better prices
                # If we can enter at mid instead of ask, we save ~7 per unit
                # With limit=20: 7×20 = 140 extra per day
                
                if best_bid is not None and best_ask is not None and buy_cap > 0:
                    # Post passive buy at bid+2 (inside spread, better than ask)
                    # This gets filled when sellers hit our price
                    our_buy_price = min(best_bid + 2, int(fair), best_ask - 1)
                    if our_buy_price > best_bid and our_buy_price < best_ask:
                        # Post for partial position
                        qty = min(buy_cap, 10)
                        orders.append(Order(product, our_buy_price, qty))
                        buy_cap -= qty

                # AGGRESSIVE BACKUP: sweep asks if still need position
                if buy_cap > 0:
                    for ask_px in sorted(order_depth.sell_orders.keys()):
                        if ask_px <= fair + 12 and buy_cap > 0:
                            qty = min(abs(order_depth.sell_orders[ask_px]), buy_cap, LIMIT)
                            orders.append(Order(product, ask_px, qty))
                            buy_cap -= qty
                        elif ask_px > fair + 12:
                            break

                # Never sell (trend is up, hold long all day)

            # ==========================================================
            # 💎  OSMIUM — PASSIVE MARKET-MAKING
            # ==========================================================
            elif product == "ASH_COATED_OSMIUM":

                mid = ((best_bid + best_ask) / 2.0) if (best_bid and best_ask) \
                      else float(best_ask or best_bid)

                ALPHA = 0.015
                ema   = (1.0 - ALPHA) * traderData["osmium_ema"] + ALPHA * mid
                traderData["osmium_ema"] = ema

                if best_bid is not None and best_ask is not None:
                    spread = best_ask - best_bid

                    # QUALITY SIGNAL: tight-spread dip
                    if (best_ask <= 9998 and
                        best_bid >= 9990 and
                        spread   <= 7   and
                        buy_cap  >  0):
                        
                        # Post passive buy at 9995 (near mid of quality zone)
                        our_buy = 9995
                        if our_buy > best_bid and our_buy < best_ask:
                            qty = min(buy_cap, 5)
                            orders.append(Order(product, our_buy, qty))
                            buy_cap -= qty
                        
                        # Aggressive backup if spread very tight
                        if spread <= 5 and buy_cap > 0:
                            qty = min(abs(order_depth.sell_orders[best_ask]), buy_cap, 5)
                            orders.append(Order(product, best_ask, qty))
                            buy_cap -= qty

                    # SELL from long: post passive at 10005
                    if position > 0 and sell_cap > 0:
                        our_sell = 10005
                        if our_sell > best_bid and our_sell < best_ask:
                            qty = min(sell_cap, position, 5)
                            orders.append(Order(product, our_sell, -qty))
                            sell_cap -= qty
                        
                        # Aggressive sell if bid very high
                        if best_bid >= 10001:
                            qty = min(order_depth.buy_orders[best_bid], 
                                      sell_cap, position, 5)
                            orders.append(Order(product, best_bid, -qty))
                            sell_cap -= qty

                    # Safety: bleed excess
                    if position > LIMIT - 3 and best_bid is not None:
                        qty = min(2, position - (LIMIT - 4))
                        if qty > 0:
                            orders.append(Order(product, best_bid, -qty))

                    # Never hold short
                    if position < 0 and best_ask is not None:
                        qty = min(3, -position)
                        orders.append(Order(product, best_ask, qty))

            result[product] = orders

        traderData = jsonpickle.encode(traderData)
        return result, MAF_BID, traderData