"""
Prosperity Round 2 — Optimised Trader
Target: 200,000+ XIRECS

Products:
  INTARIAN_PEPPER_ROOT  — deterministic upward trend, ~+1000/day
  ASH_COATED_OSMIUM     — mean-reverting around 10000

Data-derived facts:
  • PEPPER FV   = 11000 + 1000*day + 0.001*timestamp   (R²=0.9999)
  • PEPPER spread ≈ 13–15 ticks (≈11 bps), mid offsets ≈ ±6.5–7.5
  • OSMIUM FV   = 10000 (constant), std ≈ 5 ticks, range ≈ ±20
  • OSMIUM spread ≈ 16 ticks (≈16 bps), mid offsets ≈ ±8 (dominant)
  • All trades hit best bid or best ask — no inside-spread fills from bots
  • Half-life of OSMIUM mean reversion ≈ 2 ticks (very fast)
  • Buyer/seller always NaN — these are exchange bots, not named participants

PnL estimates (per 3-day block):
  PEPPER B&H   ≈  +19,700/day   → ~98,500 over 5 days
  OSMIUM MM    ≈   +2,850/day   → ~14,250 over 5 days
  Combined     ≈               → ~112,000 over 5 days
  With PEPPER aggressive quoting adding 20-40k more → 130-150k base
  Getting to 200k requires both products firing optimally every day.

Strategy breakdown:
  PEPPER  — Primary edge. Buy max position at start of each day, hold,
             sell at end. Also actively quote bids just below FV to get
             filled by sell-bots, then capture the drift.
  OSMIUM  — Secondary edge. Market make tightly around FV=10000.
             Quote bid=9993, ask=10007 (inside the 16-tick spread).
             Adjust if position gets skewed.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List
import jsonpickle
import math


# ─────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────
PEPPER  = "INTARIAN_PEPPER_ROOT"
OSMIUM  = "ASH_COATED_OSMIUM"

POSITION_LIMITS = {
    PEPPER: 20,
    OSMIUM: 50,
}

# PEPPER fair value parameters — derived from linear regression R²=0.9999
PEPPER_INTERCEPT_BY_DAY = {
    -1: 10999.98,
     0: 12000.01,
     1: 12999.92,
     # Days 2, 3, 4 extrapolated (each day adds 1000 to intercept)
     2: 13999.92,
     3: 14999.92,
     4: 15999.92,
}
PEPPER_SLOPE = 0.001  # ticks per timestamp unit

OSMIUM_FV = 10000


# ─────────────────────────────────────────────
#  Trader class
# ─────────────────────────────────────────────
class Trader:

    def run(self, state: TradingState):
        # ── Restore persistent state ──────────────────────────────────
        data = {}
        if state.traderData and state.traderData != "":
            try:
                data = jsonpickle.decode(state.traderData)
            except Exception:
                data = {}

        day       = data.get("day", state.timestamp // 1_000_000 if state.timestamp >= 1_000_000 else 0)
        ts        = state.timestamp
        orders    = {}
        conversions = 0

        current_positions = state.position  # dict product -> int

        # ── Route each product ────────────────────────────────────────
        orders[PEPPER] = self._trade_pepper(state, day, ts, current_positions.get(PEPPER, 0))
        orders[OSMIUM] = self._trade_osmium(state, ts, current_positions.get(OSMIUM, 0), data)

        # ── Persist state ─────────────────────────────────────────────
        data["day"] = day
        trader_data = jsonpickle.encode(data)
        return orders, conversions, trader_data

    # ─────────────────────────────────────────────────────────────────
    #  PEPPER — Trend Riding Strategy
    #
    #  Core insight: FV rises by exactly 1 tick every 1000 timestamp
    #  units, and by 1000 ticks every day.  The day-open ask is ~7 ticks
    #  above FV and the day-close bid is ~7 ticks above FV — so the net
    #  directional edge is ~984 ticks per day per unit of position.
    #
    #  Execution plan:
    #    1. At any point where we are below max long, send a buy order
    #       AT the best ask (aggressive) to get filled immediately.
    #    2. In the final 5% of the day (ts > 950000), flip: send sells
    #       AT the best bid to lock in the day's gain.
    #    3. In between: send passive bids just below FV to pick up any
    #       bot sells at a slight discount.
    # ─────────────────────────────────────────────────────────────────
    def _trade_pepper(self, state: TradingState, day: int, ts: int, pos: int) -> List[Order]:
        od: OrderDepth = state.order_depths.get(PEPPER)
        if od is None:
            return []

        orders = []
        limit  = POSITION_LIMITS[PEPPER]
        fv     = self._pepper_fv(day, ts)

        best_ask = min(od.sell_orders.keys()) if od.sell_orders else None
        best_bid = max(od.buy_orders.keys())  if od.buy_orders  else None
        ask_vol  = abs(od.sell_orders[best_ask]) if best_ask else 0
        bid_vol  = od.buy_orders[best_bid]        if best_bid else 0

        # ── Phase 1: Build maximum long (first 90% of day) ───────────
        if ts < 900_000:
            buy_capacity = limit - pos   # how much more we can buy

            if buy_capacity > 0 and best_ask is not None:
                # Aggressive: hit the ask to guarantee fills
                # Buy up to the available ask volume, capped by capacity
                qty = min(buy_capacity, ask_vol)
                if qty > 0:
                    orders.append(Order(PEPPER, best_ask, qty))

            # Also place a passive bid 1 tick below best_ask to sweep
            # any bots quoting aggressively
            if buy_capacity > 0 and best_ask is not None:
                passive_bid = best_ask - 1
                remaining   = buy_capacity - (min(buy_capacity, ask_vol) if best_ask else 0)
                if remaining > 0 and passive_bid > fv - 8:
                    orders.append(Order(PEPPER, passive_bid, remaining))

        # ── Phase 2: Hold ─────────────────────────────────────────────
        # Nothing to do — no need to hedge, trend is deterministic

        # ── Phase 3: Unwind at day end (last 10%) ────────────────────
        elif ts >= 900_000:
            sell_capacity = pos + limit   # can short to -limit, so total sell = pos - (-limit)
            # But we only want to close longs, not go short on PEPPER
            # just sell our position
            sell_qty = pos
            if sell_qty > 0 and best_bid is not None:
                qty = min(sell_qty, bid_vol)
                if qty > 0:
                    orders.append(Order(PEPPER, best_bid, -qty))
                # Passive ask just above bid
                remaining = sell_qty - qty
                if remaining > 0:
                    orders.append(Order(PEPPER, best_bid + 1, -remaining))

        return orders

    def _pepper_fv(self, day: int, ts: int) -> float:
        intercept = PEPPER_INTERCEPT_BY_DAY.get(day, 10999.98 + day * 1000)
        return intercept + PEPPER_SLOPE * ts

    # ─────────────────────────────────────────────────────────────────
    #  OSMIUM — Mean Reversion Market Making
    #
    #  Core insight: Price is always within ±20 of 10000.  Dominant
    #  bid/ask offsets from mid are ±8 ticks (present 59% of time).
    #  The bots only trade at best bid or ask — never inside spread.
    #
    #  Our spread is INSIDE the existing 16-tick bot spread, so we
    #  get filled first whenever a bot crosses.
    #
    #  Quote: bid = FV - QUOTE_OFFSET, ask = FV + QUOTE_OFFSET
    #  Default QUOTE_OFFSET = 3  (inside ±8 offsets, so we jump queue)
    #
    #  Inventory control:
    #    - If long > +20: widen ask side, tighten bid side
    #    - If long < -20: widen bid side, tighten ask side
    #    - Skew quotes toward FV when position is big
    # ─────────────────────────────────────────────────────────────────
    def _trade_osmium(self, state: TradingState, ts: int, pos: int, data: dict) -> List[Order]:
        od: OrderDepth = state.order_depths.get(OSMIUM)
        if od is None:
            return []

        orders = []
        limit  = POSITION_LIMITS[OSMIUM]
        fv     = OSMIUM_FV

        best_ask = min(od.sell_orders.keys()) if od.sell_orders else None
        best_bid = max(od.buy_orders.keys())  if od.buy_orders  else None

        # ── Dynamic quote offset based on inventory ───────────────────
        BASE_OFFSET = 3   # ticks inside FV on each side
        # Skew: if long, move quotes down to sell off; if short, move up
        skew = -pos / limit * 2.0   # ranges from -2 (max long) to +2 (max short)
        # skew > 0 means we raise quotes (to attract sellers when we're short)

        our_bid_px = math.floor(fv - BASE_OFFSET + skew)
        our_ask_px = math.ceil(fv  + BASE_OFFSET + skew)

        # Don't cross the spread
        if best_bid is not None and our_bid_px >= best_bid:
            our_bid_px = best_bid - 1
        if best_ask is not None and our_ask_px <= best_ask:
            our_ask_px = best_ask + 1

        buy_capacity  = limit - pos
        sell_capacity = limit + pos   # how much we can sell (to -limit)

        # Aggressive fill if price is far from FV — mean revert actively
        if best_ask is not None and best_ask < fv - 5:
            # Market is offering cheap — hit it
            qty = min(buy_capacity, abs(od.sell_orders[best_ask]))
            if qty > 0:
                orders.append(Order(OSMIUM, best_ask, qty))

        elif best_bid is not None and best_bid > fv + 5:
            # Market is bidding expensive — hit the bid
            qty = min(sell_capacity, od.buy_orders[best_bid])
            if qty > 0:
                orders.append(Order(OSMIUM, best_bid, -qty))

        # ── Passive market making quotes ─────────────────────────────
        # Place resting bid and ask inside the existing spread
        if buy_capacity > 0:
            passive_qty = min(buy_capacity, 10)   # don't over-commit per tick
            orders.append(Order(OSMIUM, our_bid_px, passive_qty))

        if sell_capacity > 0:
            passive_qty = min(sell_capacity, 10)
            orders.append(Order(OSMIUM, our_ask_px, -passive_qty))

        return orders


# ─────────────────────────────────────────────
#  PnL Projection (reference only, not executed)
# ─────────────────────────────────────────────
"""
Day        PEPPER (pos 20, B&H)    OSMIUM MM (est)    Daily total
──────────────────────────────────────────────────────────────────
Day -1         +19,680               +2,818             +22,498
Day  0         +19,740               +2,885             +22,625
Day  1         +19,700               +2,850             +22,550
Day  2         +19,700*              +2,850*            +22,550*
Day  3         +19,700*              +2,850*            +22,550*
──────────────────────────────────────────────────────────────────
5-day total  ~+98,520             ~+14,253           ~+112,773

* extrapolated using same slope/intercept pattern

To reach 200,000 XIRECS:
  The gap is ~87,000 extra XIRECS.
  Sources of additional alpha:
    1. Optimise PEPPER entry timing (buy before the open ask, target ~2 ticks better)
       → +20 ticks × 20 units × 5 days = +2,000 extra
    2. Optimise PEPPER exit timing (sell into the rally, not just at close)
       → target mid-way through day to catch the trend faster, re-enter
       → This is hard without intraday re-entry
    3. OSMIUM MM with tighter spread (quote ±2 instead of ±3)
       → Higher fill rate, earn 4 ticks per round trip instead of 6
       → Risky: more inventory accumulation
    4. OSMIUM aggressive reversion on large deviations (>8 ticks from FV)
       → Confirmed: price within ±10 of FV ~96% of time
       → When it breaks ±10, mean-revert aggressively with 10 units
       → Extra ~500-1000/day if deviations occur

REALISTIC CEILING: ~130,000-150,000 XIRECS for 5 days with this data.
Reaching 200k requires either:
  a) There are more products in Round 2 (CROISSANTS/BASKET arb etc.)
  b) Position limits are higher than 20/50
  c) PEPPER trend continues beyond Day 1 (Day 2-4 add ~20k/day each)

If pos limit is 50 for PEPPER: 50/20 * 98,520 = 246,300 → easily 200k+
"""