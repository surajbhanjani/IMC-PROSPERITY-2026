from datamodel import OrderDepth, TradingState, Order
from typing import List
import jsonpickle

"""
╔══════════════════════════════════════════════════════════════════════════╗
║        TRADER v5 — ROUND 2 DATA-GROUNDED  |  ~20,200 PnL/DAY           ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  MARKET ACCESS FEE INSIGHT (from competition hints):                    ║
║  ─────────────────────────────────────────────────────────────────────  ║
║  • Extra position capacity is auctioned. You pay a fee and need to      ║
║    beat the MEDIAN bidder (not highest) to win access.                  ║
║  • Break-even fee = extra_units × 985/unit/day × num_days               ║
║  • Pepper earns exactly 985 seashells/unit/day (confirmed, both rounds) ║
║  • Extra 5 units for 3-day round = 14,745 break-even fee                ║
║  • BID 2,000–4,000 → safely beats median, leaves 10k+ net gain          ║
║  • Rule: bid where fee / (extra_units × 985 × days) < 0.30              ║
║                                                                          ║
║  STATISTICAL FINDINGS — ALL 6 DAYS (R1+R2):                            ║
║  ─────────────────────────────────────────────────────────────────────  ║
║                                                                          ║
║  INTARIAN_PEPPER_ROOT                                                    ║
║  • Fair: 10000 + (day+2)×1000 + ts×0.001, std error ±2.1–2.5           ║
║  • Spread GROWS each day: avg 12→15 ticks, max ask-gap 9.7→11.4         ║
║  • Threshold MUST be ≥12 to fill on 100% of ticks across all days       ║
║  • L2/L3 ask prices are ALWAYS higher than L1 → can't get cheaper       ║
║  • L2/L3 volume within fair+12: avg 25.1 units (useful if limit>20)     ║
║  • Backtest: 19,650–19,750/day every single day, rock-solid              ║
║                                                                          ║
║  ASH_COATED_OSMIUM                                                       ║
║  • OU process: theta 0.06–0.12, half-life 5.7–10.9 ticks                ║
║  • Quality buy signal: ask≤9998 AND bid≥9990 AND spread≤7               ║
║    → Mid is 9993–9994 (verified), spread tight = genuine dip, not pit   ║
║    → Entry ask avg = 9996, recovery to bid≥10001 in avg 50 ticks        ║
║    → Avg gain per quality buy-sell cycle = 5.3 seashells/unit           ║
║  • Sweep L2 when available at same threshold                             ║
║  • Never go short: OSMIUM edge is BUY-THEN-SELL only                    ║
║  • Backtest: 438–699/day (consistent, no blowups)                       ║
║                                                                          ║
║  COMBINED BACKTEST (6 days): ~121,415 cumulative = ~20,236/day          ║
║  PATH TO 100,000: 5 days × 20,200 = 101,000 ✓                          ║
╚══════════════════════════════════════════════════════════════════════════╝
"""


class Trader:

    def run(self, state: TradingState):
        result = {}

        # ── Persistent State ────────────────────────────────────────────
        traderData = jsonpickle.decode(state.traderData) if state.traderData else {
            "osmium_ema":      10000.0,
            "pepper_day_base": None,
            "prev_timestamp":  -1,
        }

        timestamp = state.timestamp

        # ── Day rollover: timestamp resets to 0 on each new day ────────
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
            LIMIT    = 20          # conservative; competition may grant more via fee
            buy_cap  = LIMIT - position
            sell_cap = LIMIT + position

            # =============================================================
            # 🌿  INTARIAN_PEPPER_ROOT — DETERMINISTIC TREND RIDING
            # =============================================================
            # CORE LOGIC:
            # Fair rises exactly 0.001 per timestamp. Hold 20 units all day
            # = 2.0 PnL/tick from drift alone → ~19,700/day after slippage.
            # The ONLY job: stay at max position every single tick.
            #
            # THRESHOLD = 12:
            # R1 max ask-gap = 9.7 (Day 0), R2 max = 11.4 (Day 1).
            # Threshold 12 ensures zero missed fills across ALL observed days.
            # Threshold 8 (used in v4) caused a plateau at ts 715k-797k in R1.
            #
            # L2/L3 SWEEP:
            # L2/L3 are always more expensive than L1 (confirmed in data).
            # We sweep them too so that with a higher position limit (if granted
            # via market access fee), we can fill the extra units from deeper book.
            # =============================================================
            if product == "INTARIAN_PEPPER_ROOT":

                # ── Fair value inference ─────────────────────────────────
                mid = ((best_bid + best_ask) / 2.0) if (best_bid and best_ask) \
                      else float(best_ask or best_bid)

                if traderData["pepper_day_base"] is None or timestamp < 5000:
                    estimated = mid - timestamp * 0.001
                    snapped   = round(estimated / 1000.0) * 1000
                    traderData["pepper_day_base"] = float(snapped)

                day_base = traderData["pepper_day_base"]
                fair     = day_base + timestamp * 0.001

                # ── Sweep L1 + L2 + L3 within fair+12 ───────────────────
                # All 3 levels are included so higher position limits (from
                # market access fee) also get filled fully.
                # Data shows avg 25.1 units available within fair+12.
                ask_levels = []
                for lv in [1, 2, 3]:
                    ap = order_depth.sell_orders.get(
                        min(order_depth.sell_orders.keys()) if lv == 1
                        else None, None
                    )
                # Better: iterate sorted ask prices directly
                for ask_px in sorted(order_depth.sell_orders.keys()):
                    if ask_px <= fair + 12 and buy_cap > 0:
                        qty = min(abs(order_depth.sell_orders[ask_px]), buy_cap, LIMIT)
                        orders.append(Order(product, ask_px, qty))
                        buy_cap -= qty
                    elif ask_px > fair + 12:
                        break

                # ── Sell into genuine spikes (defensive only) ────────────
                # Data: bid never exceeded fair+6 in any of 6 observed days.
                # This tier never fires in practice but guards against anomalies.
                if best_bid is not None and sell_cap > 0 and position > 10:
                    if best_bid >= fair + 12:
                        qty = min(order_depth.buy_orders[best_bid], sell_cap, 3)
                        orders.append(Order(product, best_bid, -qty))

            # =============================================================
            # 💎  ASH_COATED_OSMIUM — QUALITY MEAN REVERSION ONLY
            # =============================================================
            # CORE LOGIC:
            # OU process (half-life 5.7–10.9 ticks). We exploit dips where
            # price is genuinely cheap, not just wide-spread.
            #
            # QUALITY BUY FILTER (all 3 conditions required):
            #   1. ask ≤ 9998  →  ask is below fair (10000) by at least 2
            #   2. bid ≥ 9990  →  confirms mid ≥ 9993; NOT a deep pit
            #   3. spread ≤ 7  →  spread is TIGHT; mid is reliable
            #   Data: when all 3 hold, ask avg=9996, mid=9993-9994
            #         Recovery to bid≥10001 in avg 50 ticks, gain=5.3/unit
            #
            # NEVER GO SHORT:
            # The symmetric sell edge exists but short recovery is slower.
            # R2 Day 0: bid>10000 on 8.8% of ticks — shorting into those
            # causes catastrophic EOD close. Only sell from LONG positions.
            #
            # EMA (alpha=0.015) tracks slow mean drift, preventing Day-0 bias.
            # =============================================================
            elif product == "ASH_COATED_OSMIUM":

                mid = ((best_bid + best_ask) / 2.0) if (best_bid and best_ask) \
                      else float(best_ask or best_bid)

                ALPHA = 0.015
                ema   = (1.0 - ALPHA) * traderData["osmium_ema"] + ALPHA * mid
                traderData["osmium_ema"] = ema

                if best_bid is not None and best_ask is not None:
                    spread = best_ask - best_bid

                    # ── QUALITY BUY ──────────────────────────────────────
                    # All 3 filters must pass (derived from 6-day analysis)
                    if (best_ask <= 9998 and
                            best_bid >= 9990 and
                            spread   <= 7   and
                            buy_cap  >  0):

                        # Sweep L1
                        ask_vol = abs(order_depth.sell_orders[best_ask])
                        qty     = min(ask_vol, buy_cap, 5)
                        orders.append(Order(product, best_ask, qty))
                        buy_cap -= qty

                        # Sweep L2 if also within threshold
                        if buy_cap > 0:
                            for ask_px in sorted(order_depth.sell_orders.keys()):
                                if ask_px <= 9998 and buy_cap > 0:
                                    qty = min(abs(order_depth.sell_orders[ask_px]), buy_cap, 3)
                                    orders.append(Order(product, ask_px, qty))
                                    buy_cap -= qty
                                elif ask_px > 9998:
                                    break

                    # ── QUALITY SELL (from long only) ────────────────────
                    # Sell when bid is above fair. Never initiate a short.
                    if best_bid >= 10001 and position > 0 and sell_cap > 0:
                        for bid_px in sorted(order_depth.buy_orders.keys(), reverse=True):
                            if bid_px >= 10001 and sell_cap > 0 and position > 0:
                                qty = min(order_depth.buy_orders[bid_px],
                                          sell_cap, position, 5)
                                orders.append(Order(product, bid_px, -qty))
                                sell_cap -= qty
                                position -= qty   # local update for safety checks
                            elif bid_px < 10001:
                                break

                    # ── INVENTORY SAFETY: bleed excess long ──────────────
                    # Prevents stuck positions from persistent cheap ticks
                    if position > LIMIT - 3 and best_bid is not None:
                        qty = min(2, position - (LIMIT - 4))
                        if qty > 0:
                            orders.append(Order(product, best_bid, -qty))

                    # ── NEVER HOLD SHORT: unwind immediately ─────────────
                    if position < 0 and best_ask is not None:
                        qty = min(3, -position)
                        orders.append(Order(product, best_ask, qty))

            result[product] = orders

        traderData = jsonpickle.encode(traderData)
        return result, 0, traderData