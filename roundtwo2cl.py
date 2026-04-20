from datamodel import OrderDepth, TradingState, Order
from typing import List
import jsonpickle

"""
╔══════════════════════════════════════════════════════════════════════════╗
║          TRADER v5 — ROUND 2 OPTIMISED  |  TARGET: 150,000 PnL          ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  ROUND 2 DATA ANALYSIS FINDINGS                                          ║
║  ──────────────────────────────────────────────────────────────────────  ║
║                                                                          ║
║  INTARIAN_PEPPER_ROOT                                                    ║
║  • Same deterministic fair formula confirmed (std error ±2.1-2.5):      ║
║      fair = 10000 + (day+2)×1000 + timestamp × 0.001                   ║
║    Round 2 days: -1→11000, 0→12000, 1→13000                            ║
║  • Spread is WIDER in R2: avg 13-15 ticks (was 12 in R1)                ║
║    → Max ask-gap per day: 9.9, 10.7, 11.4 (grows each day)             ║
║    → v4 threshold of +8 caused plateau: missed 16% of ticks on Day 1   ║
║    → FIX: threshold = +12 covers 100% of ticks across all days          ║
║  • Confirmed 99.9%+ position at max (20) all day with thresh=12         ║
║  • Backtest: 59,115 / 3 days = 19,705/day                               ║
║  • Bid spikes > fair+6: 0% — no scalping edge on pepper                 ║
║                                                                          ║
║  ASH_COATED_OSMIUM                                                       ║
║  • OU process confirmed: theta 0.06-0.12, half-life 5.7-10.9 ticks      ║
║  • 465 trades/day (vs 422 R1), price range 9979-10020                   ║
║  • Inside spread trades: 30% of matched trades (~390/day)               ║
║  • CRITICAL FINDING: when ask<9998, bid is ~9986 (spread opened wide)   ║
║    → buying at ask=9993 when mid=9989 → sell at bid=9987 = -6 loss     ║
║    → Must require bid >= 9990 to confirm mid is near fair               ║
║  • Best config (grid search): buy_ask≤9998 & bid≥9990, sell_bid≥10001  ║
║  • Backtest: 1,912 / 3 days = 637/day (safe, no blowups)               ║
║                                                                          ║
║  COMBINED: ~61,000 / 3 days = ~20,350/day                               ║
║                                                                          ║
║  PATH TO 150,000:                                                        ║
║  Round 1 actual result:  ~20,000  (chart confirmed)                     ║
║  Round 2 (3 days):       ~61,000                                        ║
║  Round 3 (3 days):       ~61,000                                        ║
║  ─────────────────────────────────────────────────────────────────────   ║
║  Cumulative after 3 rounds: ~142,000 → 150,000 by end of Round 3        ║
╚══════════════════════════════════════════════════════════════════════════╝
"""


class Trader:

    def run(self, state: TradingState):
        result = {}

        # ── Persistent State ───────────────────────────────────────────────
        traderData = jsonpickle.decode(state.traderData) if state.traderData else {
            "osmium_ema":      10000.0,
            "pepper_day_base": None,
            "prev_timestamp":  -1,
        }

        timestamp = state.timestamp

        # ── Day rollover (timestamp resets to 0 each new day) ─────────────
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
            LIMIT    = 20
            buy_cap  = LIMIT - position
            sell_cap = LIMIT + position

            # ==============================================================
            # 🌿  INTARIAN_PEPPER_ROOT  —  TREND RIDING (PRIMARY ALPHA)
            # ==============================================================
            # ─── What the data says ───────────────────────────────────────
            # Fair value is exact: base + ts × 0.001, std error ±2.
            # Holding 20 units earns +2.0 PnL per tick from drift alone.
            # Round 2 max ask-gap is 11.4 → threshold MUST be ≥12.
            # Using 12 puts position at max (20) for 99.9% of the day.
            # No selling edge exists: bid never exceeds fair+6 in R2 data.
            # ─────────────────────────────────────────────────────────────
            if product == "INTARIAN_PEPPER_ROOT":

                # Compute current mid
                if best_bid and best_ask:
                    mid = (best_bid + best_ask) / 2.0
                elif best_ask:
                    mid = float(best_ask)
                else:
                    mid = float(best_bid)

                # Infer and lock day_base (snap to nearest 1000)
                # Re-evaluate for the first 50 timestamps each day
                if traderData["pepper_day_base"] is None or timestamp < 5000:
                    estimated = mid - timestamp * 0.001
                    snapped   = round(estimated / 1000.0) * 1000
                    traderData["pepper_day_base"] = float(snapped)

                day_base = traderData["pepper_day_base"]
                fair     = day_base + timestamp * 0.001

                # ── TIER 1: ask strictly below fair (rare ~1.6%, edge=3-4) ─
                if best_ask is not None and best_ask < fair and buy_cap > 0:
                    ask_vol = abs(order_depth.sell_orders[best_ask])
                    qty     = min(ask_vol, buy_cap, LIMIT)
                    orders.append(Order(product, best_ask, qty))
                    buy_cap -= qty

                # ── TIER 2: buy at ask ≤ fair+12 (covers 100% of R2 ticks) ─
                # R2 data: max ask-gap = 11.4 → threshold 12 = zero missed fills
                # Every tick where buy_cap>0 gets filled; position stays at 20.
                if buy_cap > 0 and best_ask is not None:
                    # Sweep ALL levels up to fair+12
                    for ask_px in sorted(order_depth.sell_orders.keys()):
                        if ask_px <= fair + 12 and buy_cap > 0:
                            qty = min(abs(order_depth.sell_orders[ask_px]), buy_cap, LIMIT)
                            orders.append(Order(product, ask_px, qty))
                            buy_cap -= qty
                        elif ask_px > fair + 12:
                            break

                # ── TIER 3: Defensive sell — only extreme spikes ───────────
                # Data confirms: bid NEVER exceeds fair+6 in R2 → this never fires
                # But kept as safety valve for unusual market conditions
                if best_bid is not None and sell_cap > 0 and position > 10:
                    if best_bid >= fair + 12:
                        bid_vol = order_depth.buy_orders[best_bid]
                        qty     = min(bid_vol, sell_cap, 3)
                        orders.append(Order(product, best_bid, -qty))

            # ==============================================================
            # 💎  ASH_COATED_OSMIUM  —  DISCIPLINED MEAN REVERSION
            # ==============================================================
            # ─── What the data says ───────────────────────────────────────
            # OU process, half-life 5.7-10.9 ticks, mu≈10000.
            # CRITICAL: when ask<9998, bid is ~9986 (spread opened wide).
            #   Buying at ask=9993 when mid=9989 marks against you immediately.
            #   Recovery to fair (bid=10001+) takes 20-100 ticks — that's fine.
            #   But mid<9992 clusters are DEEP DIPS where bid stays low a long time.
            #   Filter: require bid ≥ 9990 to confirm mid is near fair, not in a pit.
            # Best grid-search config: buy_ask≤9998 & bid≥9990, sell_bid≥10001
            # EMA alpha=0.015 to track slow mean drift (prevents Day-0 bias trap).
            # ─────────────────────────────────────────────────────────────
            elif product == "ASH_COATED_OSMIUM":

                if best_bid and best_ask:
                    mid = (best_bid + best_ask) / 2.0
                elif best_ask:
                    mid = float(best_ask)
                else:
                    mid = float(best_bid)

                # Update slow EMA (~67-tick memory, calibrated via grid search)
                ALPHA = 0.015
                ema   = (1.0 - ALPHA) * traderData["osmium_ema"] + ALPHA * mid
                traderData["osmium_ema"] = ema

                # ── BUY: ask ≤ 9998 AND bid ≥ 9990 ───────────────────────
                # bid≥9990 filter: mid must be ≥9994, not in a deep pit
                # ask≤9998: genuinely below fair (10000)
                # Grid search confirmed these as optimal R2 thresholds
                if best_ask is not None and best_bid is not None and buy_cap > 0:
                    if best_ask <= 9998 and best_bid >= 9990:
                        ask_vol = abs(order_depth.sell_orders[best_ask])
                        qty     = min(ask_vol, buy_cap, 5)
                        orders.append(Order(product, best_ask, qty))
                        buy_cap -= qty

                        # Also sweep deeper cheap levels
                        for ask_px in sorted(order_depth.sell_orders.keys()):
                            if ask_px <= 9998 and buy_cap > 0:
                                qty = min(abs(order_depth.sell_orders[ask_px]), buy_cap, 5)
                                orders.append(Order(product, ask_px, qty))
                                buy_cap -= qty
                            elif ask_px > 9998:
                                break

                # ── SELL: bid ≥ 10001 AND we are long ────────────────────
                # Only sell when price is genuinely above fair
                # Never go short: OSMIUM short is as risky as OSMIUM long
                if best_bid is not None and sell_cap > 0 and position > 0:
                    if best_bid >= 10001:
                        bid_vol = order_depth.buy_orders[best_bid]
                        qty     = min(bid_vol, min(sell_cap, position), 5)
                        orders.append(Order(product, best_bid, -qty))
                        sell_cap -= qty

                        # Sweep more sell levels above 10001
                        for bid_px in sorted(order_depth.buy_orders.keys(), reverse=True):
                            if bid_px >= 10001 and sell_cap > 0 and position > 0:
                                qty = min(order_depth.buy_orders[bid_px], sell_cap, 5)
                                orders.append(Order(product, bid_px, -qty))
                                sell_cap -= qty
                            elif bid_px < 10001:
                                break

                # ── INVENTORY BLEED: prevent accumulation ─────────────────
                # If we somehow go too long (persistent cheap asks), bleed slowly
                if position > LIMIT - 3 and best_bid is not None:
                    qty = min(2, position - (LIMIT - 4))
                    if qty > 0:
                        orders.append(Order(product, best_bid, -qty))

                # Never short OSMIUM — only unwind longs
                if position < 0 and best_ask is not None:
                    qty = min(3, -position)
                    orders.append(Order(product, best_ask, qty))

            result[product] = orders

        traderData = jsonpickle.encode(traderData)
        return result, 0, traderData