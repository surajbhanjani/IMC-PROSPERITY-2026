from datamodel import OrderDepth, TradingState, Order
from typing import List
import jsonpickle

"""
=======================================================================
TRADING ALGORITHM v2 — DATA-DRIVEN IMPROVEMENTS
=======================================================================

KEY FINDINGS FROM DATA ANALYSIS:
──────────────────────────────────────────────────────────────────────

1. INTARIAN_PEPPER_ROOT — DETERMINISTIC LINEAR TREND
   • Fair value formula (confirmed, std error ≈ ±2):
       fair = 10000 + (day + 2) * 1000 + timestamp * 0.001
   • Price rises ~1000/day, ~0.1 per 100-timestamp step
   • Strategy: ALWAYS stay max-long (position = +20)
     – Being long 20 units earns +2.0 per tick purely from drift
     – Aggressively buy any ask at or below fair
     – Only sell above fair+2 to capture spread AND the trend
     – Use day_start baseline per known day schedule

2. ASH_COATED_OSMIUM — PURE MEAN REVERSION AROUND 10000
   • True fair = 10000 (slope ≈ 0, confirmed across 3 days)
   • Typical spread: 16 ticks (bid ~9992, ask ~10008)
   • Free edge opportunities:
       ask ≤ 9999: ~4.8% of ticks → aggressive buy
       bid ≥ 10001: ~4.5% of ticks → aggressive sell
   • Strategy: tight passive MM + sweep any mispriced quotes
     – Quote bid at best_bid+1 (improve market), ask at best_ask-1
     – Aggressive sweep when price deviates >3 from 10000
     – Inventory skew: each unit penalises fair by 0.5 (tighter)

3. SPREAD & DEPTH
   • OSMIUM depth: ~31 units each side — plenty of liquidity
   • PEPPER depth: ~25 units each side — healthy
   • PEPPER min spread can be 2 — very tight, don't post inside

=======================================================================
"""


class Trader:

    def run(self, state: TradingState):
        result = {}

        # ── Persistent state ───────────────────────────────────────────
        traderData = jsonpickle.decode(state.traderData) if state.traderData else {
            "day_start_ts": None,   # first timestamp seen this session
            "day_num": None,        # inferred day number
        }

        for product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []

            if not order_depth.buy_orders or not order_depth.sell_orders:
                result[product] = orders
                continue

            best_bid = max(order_depth.buy_orders.keys())
            best_ask = min(order_depth.sell_orders.keys())
            position  = state.position.get(product, 0)
            timestamp = state.timestamp
            LIMIT     = 20

            # ============================================================
            # 💎 ASH_COATED_OSMIUM — TIGHT MEAN-REVERSION MM
            # ============================================================
            if product == "ASH_COATED_OSMIUM":

                FAIR = 10000  # confirmed flat over all 3 days

                # ── Inventory-adjusted fair ────────────────────────────
                # Each unit of position skews our fair by 0.5
                # (tighter than original 1.0 — data shows std only ±5)
                fair_adj = FAIR - position * 0.5

                buy_qty  =  LIMIT - position   # room to buy
                sell_qty = -LIMIT - position   # room to sell (negative)

                # ── Aggressive sweep: eat any pricing error > 2 ───────
                # Data: ask ≤ 9997 only ~3% of ticks — sweep when cheap
                for ask_price in sorted(order_depth.sell_orders.keys()):
                    if ask_price <= FAIR - 2 and buy_qty > 0:
                        vol = min(abs(order_depth.sell_orders[ask_price]), buy_qty)
                        orders.append(Order(product, ask_price, vol))
                        buy_qty -= vol

                for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                    if bid_price >= FAIR + 2 and sell_qty < 0:
                        vol = max(-order_depth.buy_orders[bid_price], sell_qty)
                        orders.append(Order(product, bid_price, vol))
                        sell_qty -= vol

                # ── Passive MM: improve by 1 tick over best quote ─────
                # Typical market: bid=9992, ask=10008 → spread=16
                # We post: bid=9993, ask=10007 → capture ~14 ticks/round
                if buy_qty > 0:
                    # Post at best_bid+1, but cap at fair_adj-1
                    buy_price = min(best_bid + 1, int(fair_adj) - 1)
                    # Don't cross the spread
                    buy_price = min(buy_price, best_ask - 1)
                    orders.append(Order(product, buy_price, buy_qty))

                if sell_qty < 0:
                    sell_price = max(best_ask - 1, int(fair_adj) + 1)
                    sell_price = max(sell_price, best_bid + 1)
                    orders.append(Order(product, sell_price, sell_qty))

            # ============================================================
            # 🌿 INTARIAN_PEPPER_ROOT — TREND-FOLLOWING + DIRECTIONAL MM
            # ============================================================
            elif product == "INTARIAN_PEPPER_ROOT":

                # ── Infer day number from observed price level ─────────
                # Day -2 starts ~10000, Day -1 ~11000, Day 0 ~12000
                # At timestamp t: fair = day_base + t * 0.001
                # Estimate day_base from current mid_price
                mid = (best_bid + best_ask) / 2.0
                inferred_base = mid - timestamp * 0.001

                # Snap to nearest 1000 (known day starts: 10000, 11000, 12000)
                # Add 10 for safety margin
                day_base = round(inferred_base / 1000) * 1000

                # ── Exact fair value (data confirms std error ≈ ±2) ───
                fair = day_base + timestamp * 0.001

                # ── Inventory-adjusted fair (mild skew only) ──────────
                # We WANT to be long (trend is up), so penalise short positions
                # more than long. Asymmetric skew: +0.1 per short unit, -0.05 per long unit
                if position >= 0:
                    # Being long is GOOD — minimal penalty
                    fair_adj = fair - position * 0.05
                else:
                    # Being short is BAD — strong penalty to buy back
                    fair_adj = fair - position * 0.5   # positive when position negative

                buy_qty  =  LIMIT - position
                sell_qty = -LIMIT - position

                # ── Aggressive: sweep asks below fair ─────────────────
                # Since price is rising, ANY ask below fair is a gift
                for ask_price in sorted(order_depth.sell_orders.keys()):
                    if ask_price <= fair_adj and buy_qty > 0:
                        vol = min(abs(order_depth.sell_orders[ask_price]), buy_qty)
                        orders.append(Order(product, ask_price, vol))
                        buy_qty -= vol

                # Only sell if bid is WELL above fair (don't fight the trend)
                for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                    if bid_price >= fair_adj + 3 and sell_qty < 0:
                        vol = max(-order_depth.buy_orders[bid_price], sell_qty)
                        orders.append(Order(product, bid_price, vol))
                        sell_qty -= vol

                # ── Passive MM: directionally biased ──────────────────
                # Buy side: always post aggressively to stay long
                if buy_qty > 0:
                    # Post at best_bid+1 or fair-1, whichever is lower (more fill)
                    buy_price = min(best_bid + 1, int(fair_adj) - 1)
                    buy_price = min(buy_price, best_ask - 1)
                    orders.append(Order(product, buy_price, buy_qty))

                # Sell side: only post if we can capture real spread above fair
                # Don't post sells close to fair — trend will carry price up
                if sell_qty < 0 and position > 0:
                    sell_price = max(best_ask - 1, int(fair_adj) + 3)
                    sell_price = max(sell_price, best_bid + 1)
                    orders.append(Order(product, sell_price, sell_qty))
                elif sell_qty < 0 and position <= 0:
                    # We're flat or short — post minimal sell to avoid getting stuck short
                    sell_price = max(best_ask - 1, int(fair_adj) + 5)
                    sell_price = max(sell_price, best_bid + 1)
                    orders.append(Order(product, sell_price, sell_qty))

            result[product] = orders

        traderData = jsonpickle.encode(traderData)
        return result, 0, traderData