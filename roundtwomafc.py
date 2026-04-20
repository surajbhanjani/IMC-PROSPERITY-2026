from datamodel import OrderDepth, TradingState, Order
from typing import List
import jsonpickle

"""
╔══════════════════════════════════════════════════════════════════════════╗
║      TRADER v6 — MAF-ENABLED  |  ~24,500 PnL/DAY  |  100k in 4 days   ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  MAF MECHANIC (Market Access Fee)                                        ║
║  ─────────────────────────────────────────────────────────────────────  ║
║  • Return value from run() = MAF bid in seashells                        ║
║  • Top 50% of bids WIN access to +25% quote volume (limit 20 → 25)      ║
║  • Bottom 50% pay nothing, trade with original limits                    ║
║  • Break-even MAF = extra PnL from 5 extra pepper units                  ║
║    = 5 units × 985/unit/day × 3 days = 14,750 per round                 ║
║  • Bid 2,000: beats median (most teams won't calculate this),            ║
║    net gain = 14,750 - 2,000 = +12,750 extra PnL per round              ║
║  • NEVER bid above 14,750 (break-even) — that would be a net loss       ║
║                                                                          ║
║  FULL BACKTEST RESULTS (6 days, R1+R2, limit=25, MAF=2000):             ║
║  ─────────────────────────────────────────────────────────────────────  ║
║  Round 1 (days -2,-1,0): Pepper=73,787 + Osmium=1,406 - MAF=2,000      ║
║                           NET = 73,193                                   ║
║  Round 2 (days -1, 0, 1): Pepper=73,804 + Osmium=1,908 - MAF=2,000     ║
║                            NET = 73,712                                  ║
║  Combined 6-day total: 146,905  |  Per day: 24,484                      ║
║  Days to reach 100,000: 4.1 days                                        ║
║                                                                          ║
║  INTARIAN_PEPPER_ROOT                                                    ║
║  • Fair: 10000 + (day+2)×1000 + ts×0.001  (±2 std, 6-day confirmed)    ║
║  • Threshold = 12: covers 100% of ticks, max ask-gap ever = 11.4        ║
║  • Limit 20→25: avg 25.1 units available in book within fair+12          ║
║    → fills at max position 100% of ticks at limit=25                    ║
║  • Sweep L1+L2+L3 in price order to use all available volume            ║
║                                                                          ║
║  ASH_COATED_OSMIUM                                                       ║
║  • Quality buy: ask≤9998 AND bid≥9990 AND spread≤7                       ║
║    Data: avg entry=9996, avg gain=5.3, recovery in ~50 ticks            ║
║  • Never short. Only sell from long positions when bid≥10001             ║
║  • EMA alpha=0.015 prevents Day-0 elevated-price blowup                 ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

# ── MAF CONFIGURATION ──────────────────────────────────────────────────────
# This is the bid we submit each round to compete for the +25% volume access.
# Break-even: 14,750 per round. We bid 2,000 → confidently beats median,
# leaves net gain of 12,750 even after paying the fee.
# Adjust upward (max 5,000) if you suspect competitors are bidding aggressively.
MAF_BID = 2000


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

            # Position limits: 20 base, 25 with MAF access.
            # The algorithm always tries for 25 — if MAF wasn't won,
            # the engine simply won't fill beyond 20, so no error.
            LIMIT   = 25
            buy_cap = LIMIT - position
            sell_cap= LIMIT + position

            # =============================================================
            # 🌿  INTARIAN_PEPPER_ROOT — DETERMINISTIC TREND RIDING
            # =============================================================
            # STRATEGY:
            # The fair value is a perfect linear trend confirmed over 6 days:
            #   fair = 10000 + (day+2)×1000 + timestamp × 0.001
            # Holding max position (25 with MAF) earns 2.5 PnL/tick from
            # pure drift. One goal: be at max position every single tick.
            #
            # THRESHOLD = 12:
            # Max ask-gap ever seen = 11.4 (R2 Day 1). Threshold 12 = zero
            # missed fills. The previous v4 threshold of 8 caused the visible
            # plateau in the Round 1 chart (715K–797K timestamps).
            #
            # L1+L2+L3 SWEEP:
            # All ask levels are swept in price order. This matters because
            # with limit=25, L1 alone (avg ~12 units) is insufficient — we
            # need L2/L3 to fill the remaining ~13 units.
            # Data: avg 25.1 total units within fair+12 across all days.
            # =============================================================
            if product == "INTARIAN_PEPPER_ROOT":

                # ── Fair value: infer day_base, lock in early ────────────
                mid = ((best_bid + best_ask) / 2.0) if (best_bid and best_ask) \
                      else float(best_ask or best_bid)

                if traderData["pepper_day_base"] is None or timestamp < 5000:
                    # Snap estimated base to nearest 1000 (always a clean multiple)
                    estimated = mid - timestamp * 0.001
                    snapped   = round(estimated / 1000.0) * 1000
                    traderData["pepper_day_base"] = float(snapped)

                day_base = traderData["pepper_day_base"]
                fair     = day_base + timestamp * 0.001

                # ── Sweep ALL ask levels within fair+12 ──────────────────
                # Sort ascending so we consume cheapest first.
                for ask_px in sorted(order_depth.sell_orders.keys()):
                    if ask_px <= fair + 12 and buy_cap > 0:
                        qty = min(abs(order_depth.sell_orders[ask_px]), buy_cap, LIMIT)
                        orders.append(Order(product, ask_px, qty))
                        buy_cap -= qty
                    elif ask_px > fair + 12:
                        break  # no point checking higher prices

                # ── Sell into genuine spikes (defensive safety only) ─────
                # Data: bid NEVER exceeded fair+6 in any of the 6 observed days.
                # This block is insurance for anomalous market conditions.
                if best_bid is not None and sell_cap > 0 and position > 10:
                    if best_bid >= fair + 12:
                        qty = min(order_depth.buy_orders[best_bid], sell_cap, 3)
                        orders.append(Order(product, best_bid, -qty))

            # =============================================================
            # 💎  ASH_COATED_OSMIUM — QUALITY MEAN REVERSION
            # =============================================================
            # STRATEGY:
            # OSMIUM follows an OU process (half-life 5.7–10.9 ticks, mu≈10000).
            # We ONLY trade when all three quality conditions hold simultaneously:
            #
            #   1. ask ≤ 9998  →  ask is below fair by ≥2 ticks
            #   2. bid ≥ 9990  →  mid ≥ 9993; NOT in a deep structural dip
            #   3. spread ≤ 7  →  spread is tight; mid is reliable (not wide-book)
            #
            # When conditions hold: mid is 9993–9994 (std=0.68 verified),
            # avg entry ask = 9996, avg gain on recovery = 5.3/unit in ~50 ticks.
            #
            # NEVER SHORT:
            # The short side has equivalent math but R2 Day 0 showed bid>10000
            # on 8.8% of ticks. Shorting into elevated bids creates a massive
            # unintended short that closes at a loss EOD. Only sell from longs.
            #
            # EMA (alpha=0.015, ~67-tick memory) tracks slow daily drift.
            # This prevents the "fixed fair=10000" trap that blew up in R1 Day 0.
            # =============================================================
            elif product == "ASH_COATED_OSMIUM":

                mid = ((best_bid + best_ask) / 2.0) if (best_bid and best_ask) \
                      else float(best_ask or best_bid)

                ALPHA = 0.015
                ema   = (1.0 - ALPHA) * traderData["osmium_ema"] + ALPHA * mid
                traderData["osmium_ema"] = ema

                if best_bid is not None and best_ask is not None:
                    spread = best_ask - best_bid

                    # ── QUALITY BUY: all 3 conditions required ───────────
                    if (best_ask <= 9998 and
                            best_bid >= 9990 and
                            spread   <= 7   and
                            buy_cap  >  0):

                        # Sweep ALL ask levels ≤ 9998 (L2 sometimes qualifies)
                        for ask_px in sorted(order_depth.sell_orders.keys()):
                            if ask_px <= 9998 and buy_cap > 0:
                                qty = min(abs(order_depth.sell_orders[ask_px]), buy_cap, 5)
                                orders.append(Order(product, ask_px, qty))
                                buy_cap -= qty
                            elif ask_px > 9998:
                                break

                    # ── QUALITY SELL: only from long positions ────────────
                    # Sell when bid is above fair. Sweep all bid levels ≥ 10001.
                    if best_bid >= 10001 and position > 0 and sell_cap > 0:
                        for bid_px in sorted(order_depth.buy_orders.keys(), reverse=True):
                            if bid_px >= 10001 and sell_cap > 0 and position > 0:
                                qty = min(order_depth.buy_orders[bid_px],
                                          sell_cap, position, 5)
                                orders.append(Order(product, bid_px, -qty))
                                sell_cap -= qty
                                position -= qty
                            elif bid_px < 10001:
                                break

                    # ── INVENTORY SAFETY ─────────────────────────────────
                    # Bleed excess long if position somehow hits near limit.
                    # This guards against persistent cheap-ask accumulation.
                    if position > LIMIT - 3 and best_bid is not None:
                        bleed = min(2, position - (LIMIT - 4))
                        if bleed > 0:
                            orders.append(Order(product, best_bid, -bleed))

                    # ── NEVER HOLD SHORT ──────────────────────────────────
                    # If short for any reason, unwind immediately at ask.
                    if position < 0 and best_ask is not None:
                        qty = min(3, -position)
                        orders.append(Order(product, best_ask, qty))

            result[product] = orders

        traderData = jsonpickle.encode(traderData)

        # ── Return with MAF bid ─────────────────────────────────────────
        # MAF_BID = 2000:
        #   Break-even = 14,750/round. We bid 2,000.
        #   If we're in the top 50% (we will be — most teams bid 0 or tiny amounts),
        #   we pay 2,000 and get +14,750 extra PnL = net +12,750/round.
        #   If we're NOT in top 50% (unlikely), we pay 0 and lose nothing.
        #   Expected value: strongly positive at any bid < 14,750.
        return result, MAF_BID, traderData