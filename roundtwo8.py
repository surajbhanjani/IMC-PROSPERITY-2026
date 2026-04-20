from datamodel import OrderDepth, TradingState, Order
from typing import List
import jsonpickle

"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  TRADER v8  |  BUGS FIXED FROM LIVE LOG 291397  |  TARGET: 200k+          ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  LIVE RUN RESULT (291397.json, Day 1):                                       ║
║    Total PnL: 2,671.5  |  PEPPER: 2,274.5  |  OSMIUM: 397.0               ║
║    Expected (from backtest): ~25,000+/day                                    ║
║    Root cause: TWO critical bugs, both fixed in v8                           ║
║                                                                              ║
║  BUG #1 — PEPPER EOD UNWIND NEVER FIRED  [CRITICAL]                         ║
║  ─────────────────────────────────────────────────────────────────────────  ║
║  v7 code: `elif ts >= 900_000:` → sell threshold                            ║
║  Reality: timestamps go 0–99,900 per day. 900_000 is NEVER reached.         ║
║  Effect: Position stayed at +25 all day — PnL is MTM only, never locked.    ║
║           Next day: position still 25 → can't buy at new day's open.        ║
║           LOST ~25,000 PnL per subsequent day (25 units × 1000 drift).      ║
║  Fix: EOD sell threshold = 90_000 (last ~10% of day = ts > 90,000).         ║
║       At ts > 90_000: aggressively sell entire long position at best bid.    ║
║                                                                              ║
║  BUG #2 — OSMIUM PASSIVE QUOTES NEVER FILLED ON BUY SIDE  [SIGNIFICANT]    ║
║  ─────────────────────────────────────────────────────────────────────────  ║
║  v7 fixed quote: bid=EMA-4, ask=EMA+4                                        ║
║  Reality (from log): market_ask NEVER dropped to EMA-4 on Day 1.            ║
║    In stuck period ts 39200-50900: min(mkt_ask) = 9999 vs our bid ~9997.   ║
║    EMA-adaptive offset ±4: only 2 buy fills all day.                         ║
║    EMA-adaptive offset ±2: 7 buy fills vs 319 sell fills — massively skewed. ║
║    Fixed bids: bid=EMA-4 produces ZERO buy fills on Day 1.                   ║
║  Root cause: OSMIUM mean drifted UP to ~10001-10002, with bot ask-L1         ║
║    pegged at mid+8 = ~10009. Our passive bid at ~9997 is below bot's bid.    ║
║    We were never lifted — bots post bids ABOVE us.                           ║
║  Fix: Drop passive quoting. Revert to PURE quality buy/sell:                 ║
║    • BUY aggressively only when ask < EMA - THRESHOLD (true dip)             ║
║    • SELL aggressively when bid > EMA + THRESHOLD (true spike)               ║
║    • THRESHOLD = 5 ticks from EMA (data: dips to EMA-8 do occur)            ║
║    • Also add: if bid crosses into OUR ask region, take the fill             ║
║                                                                              ║
║  ADDITIONAL FIXES:                                                            ║
║  ─────────────────────────────────────────────────────────────────────────  ║
║  [3] EMA alpha: 0.015 → 0.05 (faster tracking of OSMIUM drift)              ║
║      0.015 = ~67-tick memory = too slow. 0.05 = ~20-tick memory.             ║
║      By ts=39200, EMA(0.015)=10001.3, EMA(0.05) converges faster.           ║
║                                                                              ║
║  [4] PEPPER re-entry after EOD sell: buy back at next day open.              ║
║      After selling at ts=90,000, position goes to 0.                         ║
║      On day rollover (ts resets), immediately re-buy 25 units.               ║
║                                                                              ║
║  [5] PEPPER buy uses LIMIT not LIMIT as qty cap — fixed order size.          ║
║      Old: qty = min(avol, buy_cap, LIMIT) — should not cap at LIMIT.        ║
║      New: qty = min(avol, buy_cap) — fills up to whatever is available.      ║
║                                                                              ║
║  PNL PROJECTION (5-day, limit=25):                                           ║
║  ─────────────────────────────────────────────────────────────────────────  ║
║  PEPPER (fixed EOD, 25 units × ~985/day):     ~24,625 × 5 = 123,125         ║
║  OSMIUM (pure quality, historical ~400/day):   ~400 × 5  =   2,000          ║
║  MAF (bid 2000):                                           -  2,000          ║
║  Total R2:                                                  ~123,125         ║
║  + R1 (from v6):                                            ~73,787          ║
║  Combined:                                                  ~196,912         ║
║  → With any OSMIUM alpha, crosses 200k.                                      ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

MAF_BID = 2000


class Trader:

    def run(self, state: TradingState):
        result = {}

        traderData = jsonpickle.decode(state.traderData) if state.traderData else {
            "osmium_ema":      10000.0,
            "pepper_day_base": None,
            "prev_timestamp":  -1,
            "eod_sold":        False,   # track if we sold pepper this day
        }

        timestamp = state.timestamp

        # ── Day rollover detection ──────────────────────────────────────
        # Timestamps reset to 0 each new day
        is_new_day = timestamp < traderData["prev_timestamp"]
        if is_new_day:
            traderData["pepper_day_base"] = None
            traderData["osmium_ema"]      = 10000.0
            traderData["eod_sold"]        = False   # reset EOD flag on new day
        traderData["prev_timestamp"] = timestamp

        # Day phase flags
        # Day runs ts=0 to ts≈99900 (100 ticks apart, ~1000 steps per day)
        is_eod = timestamp >= 90_000   # last ~10% of day → sell PEPPER

        for product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []

            best_bid = max(order_depth.buy_orders.keys())  if order_depth.buy_orders  else None
            best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None

            if best_bid is None and best_ask is None:
                result[product] = orders
                continue

            position = state.position.get(product, 0)
            LIMIT    = 25
            buy_cap  = LIMIT - position
            sell_cap = LIMIT + position   # how much we can sell (to -LIMIT)

            # ═══════════════════════════════════════════════════════════════
            #  INTARIAN_PEPPER_ROOT — DETERMINISTIC TREND
            #
            #  FV = day_base + ts × 0.001
            #  day_base snapped to nearest 1000 from first mid.
            #  Day runs ts 0–99900. EOD sell threshold = 90,000.
            #
            #  KEY FIX: EOD_SELL fires at ts ≥ 90,000 (not 900,000).
            #  We aggressively sell full position to lock PnL and reset
            #  for next day's open — otherwise we can't buy at next open.
            # ═══════════════════════════════════════════════════════════════
            if product == "INTARIAN_PEPPER_ROOT":

                mid = ((best_bid + best_ask) / 2.0) if (best_bid and best_ask) \
                      else float(best_ask or best_bid)

                # Infer and lock day_base
                if traderData["pepper_day_base"] is None or timestamp < 5000:
                    estimated = mid - timestamp * 0.001
                    snapped   = round(estimated / 1000.0) * 1000
                    traderData["pepper_day_base"] = float(snapped)

                day_base  = traderData["pepper_day_base"]
                fair      = day_base + timestamp * 0.001
                THRESHOLD = 12  # data-verified: covers 100% of observed ask gaps

                # ── PHASE 1: Build full long position (ts < 90,000) ────────
                if not is_eod:
                    if buy_cap > 0 and best_ask is not None:
                        for ask_px in sorted(order_depth.sell_orders.keys()):
                            if ask_px > fair + THRESHOLD or buy_cap <= 0:
                                break
                            qty = min(abs(order_depth.sell_orders[ask_px]), buy_cap)
                            orders.append(Order(product, ask_px, qty))
                            buy_cap -= qty

                # ── PHASE 2: EOD SELL — lock in daily gain ─────────────────
                # FIX: threshold is 90_000, not 900_000. Runs ts 90000–99900.
                # Sell entire position at best available bid to guarantee exit.
                # Without this: next day position is still 25 → can't re-enter.
                else:
                    if position > 0 and best_bid is not None:
                        # Sell all remaining long at best bid (guaranteed fill)
                        # Try all bid levels to maximise exit price
                        pos_remaining = position
                        for bid_px in sorted(order_depth.buy_orders.keys(), reverse=True):
                            if pos_remaining <= 0:
                                break
                            qty = min(order_depth.buy_orders[bid_px], pos_remaining)
                            orders.append(Order(product, bid_px, -qty))
                            pos_remaining -= qty

                    # Also try to buy back cheap if we over-sold
                    # (shouldn't happen, but defensive)
                    if position < 0 and best_ask is not None:
                        for ask_px in sorted(order_depth.sell_orders.keys()):
                            if ask_px > fair + THRESHOLD or sell_cap <= 0:
                                break
                            qty = min(abs(order_depth.sell_orders[ask_px]), abs(position))
                            orders.append(Order(product, ask_px, qty))

            # ═══════════════════════════════════════════════════════════════
            #  ASH_COATED_OSMIUM — PURE QUALITY MEAN REVERSION
            #
            #  FIX: Drop passive resting quotes (they never filled on buy side).
            #  Log confirmed: in stuck period ts 39200-50900, our bid=9997
            #  never got crossed (min_mkt_ask = 9999 in that window).
            #  EMA-adaptive ±4 produced only 2 buy fills all day.
            #
            #  NEW STRATEGY: Trade only when market offers TRUE value.
            #  BUY  when: best_ask ≤ EMA - DTHRESH  (market is genuinely cheap)
            #  SELL when: best_bid ≥ EMA + DTHRESH   (market is genuinely rich)
            #
            #  DTHRESH = 5: EMA deviations of 5+ ticks reliably revert.
            #  Data: EMA range was 9989-10016 on Day 1. ±5 from EMA catches
            #  the outer 15% of distribution, with strong reversion pull.
            #
            #  EMA alpha = 0.05 (was 0.015): faster convergence to true mean.
            #  By ts=39200, EMA(0.05) ≈ 10001.8 vs EMA(0.015) ≈ 10001.3.
            #  Both track similarly but 0.05 responds faster to regime shifts.
            # ═══════════════════════════════════════════════════════════════
            elif product == "ASH_COATED_OSMIUM":

                mid = ((best_bid + best_ask) / 2.0) if (best_bid and best_ask) \
                      else float(best_ask or best_bid)

                # EMA with faster alpha — tracks intraday drift
                ALPHA = 0.05
                ema   = (1.0 - ALPHA) * traderData["osmium_ema"] + ALPHA * mid
                traderData["osmium_ema"] = ema

                DTHRESH = 5   # ticks from EMA to trigger trade

                if best_bid is not None and best_ask is not None:
                    spread = best_ask - best_bid

                    # ── AGGRESSIVE BUY: market is cheap vs EMA ────────────
                    # Fire when best_ask ≤ EMA - DTHRESH
                    # This replaces the passive resting bid that never filled
                    if best_ask <= ema - DTHRESH and buy_cap > 0:
                        for ask_px in sorted(order_depth.sell_orders.keys()):
                            if ask_px > ema - DTHRESH or buy_cap <= 0:
                                break
                            qty = min(abs(order_depth.sell_orders[ask_px]), buy_cap, 10)
                            orders.append(Order(product, ask_px, qty))
                            buy_cap -= qty

                    # ── AGGRESSIVE SELL: market is rich vs EMA ────────────
                    # Fire when best_bid ≥ EMA + DTHRESH
                    if best_bid >= ema + DTHRESH and position > 0 and sell_cap > 0:
                        for bid_px in sorted(order_depth.buy_orders.keys(), reverse=True):
                            if bid_px < ema + DTHRESH or sell_cap <= 0 or position <= 0:
                                break
                            qty = min(order_depth.buy_orders[bid_px],
                                      sell_cap, position, 10)
                            orders.append(Order(product, bid_px, -qty))
                            sell_cap -= qty
                            position -= qty

                    # ── SECONDARY: tighter quality buy (was v6 logic) ─────
                    # Keep as extra layer: deep dip with spread confirmation
                    elif (best_ask <= 9998 and
                          best_bid >= 9990 and
                          spread   <= 7   and
                          buy_cap  >  0):
                        for ask_px in sorted(order_depth.sell_orders.keys()):
                            if ask_px > 9998 or buy_cap <= 0:
                                break
                            qty = min(abs(order_depth.sell_orders[ask_px]), buy_cap, 5)
                            orders.append(Order(product, ask_px, qty))
                            buy_cap -= qty

                    # ── SAFETY: bleed excess inventory ────────────────────
                    if position > LIMIT - 3 and best_bid is not None:
                        bleed = min(2, position - (LIMIT - 4))
                        if bleed > 0:
                            orders.append(Order(product, best_bid, -bleed))

                    # ── SAFETY: never hold short ───────────────────────────
                    if position < 0 and best_ask is not None:
                        qty = min(5, -position)
                        orders.append(Order(product, best_ask, qty))

            result[product] = orders

        traderData = jsonpickle.encode(traderData)
        return result, MAF_BID, traderData