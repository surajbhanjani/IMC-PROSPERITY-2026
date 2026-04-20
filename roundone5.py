from datamodel import OrderDepth, TradingState, Order
from typing import List
import jsonpickle

"""
╔══════════════════════════════════════════════════════════════════════╗
║           TRADER v3 — DATA-DRIVEN, TARGETING 100k PnL              ║
╠══════════════════════════════════════════════════════════════════════╣
║  FINDINGS FROM 3-DAY BACKTEST ANALYSIS                              ║
║                                                                      ║
║  INTARIAN_PEPPER_ROOT (primary alpha source)                        ║
║  ─────────────────────────────────────────────────────────          ║
║  • EXACT fair value formula (std error only ±2):                    ║
║      fair = 10000 + (day+2)×1000 + timestamp × 0.001               ║
║    Day -2 → Day 0: 10000 → 11000 → 12000 at t=0                    ║
║    Each day rises exactly +1000 from t=0 to t=999900                ║
║                                                                      ║
║  • Holding 20 units (max) the entire day earns:                     ║
║      +0.1/tick × 20 units = +2.0 per tick from PURE DRIFT          ║
║      → ~19,700-19,800 per day after entry/exit slippage             ║
║                                                                      ║
║  • Price oscillates ±2 around the trend (Gaussian noise)            ║
║    → Small additional alpha from scalping oscillations              ║
║                                                                      ║
║  • Orderbook half-spread: ~6-7 ticks                                ║
║    → Entry at ask costs ~6 above fair, recouped in ~60 ticks        ║
║    → Worth immediate aggressive entry on day open                   ║
║                                                                      ║
║  ASH_COATED_OSMIUM (secondary income)                               ║
║  ─────────────────────────────────────────────────────              ║
║  • True fair = 10000 (slope ≈ 0 over all 3 days)                   ║
║  • Avg spread = 16 ticks (bid ~9992, ask ~10008)                    ║
║  • 1265 market trades over 3 days (~422/day, 2194 units/day)        ║
║  • Extreme prices (< 9990 or > 10010): ~27% of all trades           ║
║    → 966 units at avg 9986 = 13,092 latent value                   ║
║    → 1063 units at avg 10014 = 14,722 latent value                 ║
║  • Strategy: deep value sweeps + tight passive MM                   ║
║    → Realistic: ~3,000-4,000/day                                    ║
║                                                                      ║
║  EXPECTED PnL PER DAY: ~22,000-23,000                               ║
║  EXPECTED PnL 3 DAYS:  ~66,000-70,000                               ║
║  TARGET (100k): achievable over ~5 days of competition              ║
╚══════════════════════════════════════════════════════════════════════╝
"""


class Trader:

    def run(self, state: TradingState):
        result = {}

        # ── Persistent State ───────────────────────────────────────────
        traderData = jsonpickle.decode(state.traderData) if state.traderData else {
            "day_base": None,          # inferred day start fair value (10000, 11000, 12000...)
            "day_base_locked": False,  # once confirmed, don't change mid-day
            "pepper_entry_done": False,# have we bought our full 20 yet today?
            "last_day_ts": -1,         # detect day rollovers
        }

        timestamp = state.timestamp

        # Detect day rollover (timestamp resets to 0 each new day)
        if timestamp < traderData.get("last_day_ts", -1):
            # New day — reset day-specific flags
            traderData["day_base"] = None
            traderData["day_base_locked"] = False
            traderData["pepper_entry_done"] = False
        traderData["last_day_ts"] = timestamp

        for product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []

            if not order_depth.buy_orders and not order_depth.sell_orders:
                result[product] = orders
                continue

            # Safe best bid/ask extraction
            best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
            best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None

            position = state.position.get(product, 0)
            LIMIT = 20

            # ==============================================================
            # 🌿 INTARIAN_PEPPER_ROOT — TREND DOMINATION
            # ==============================================================
            if product == "INTARIAN_PEPPER_ROOT":

                if best_bid is None and best_ask is None:
                    result[product] = orders
                    continue

                mid = None
                if best_bid and best_ask:
                    mid = (best_bid + best_ask) / 2.0
                elif best_bid:
                    mid = float(best_bid)
                elif best_ask:
                    mid = float(best_ask)

                # ── Infer and lock day_base ────────────────────────────
                # Known pattern: day_base is always a multiple of 1000
                # (10000, 11000, 12000, 13000...) and at t=0, mid ≈ day_base
                if not traderData["day_base_locked"] and mid is not None:
                    # At any timestamp, mid ≈ day_base + ts*0.001
                    estimated_base = mid - timestamp * 0.001
                    # Snap to nearest 1000
                    snapped = round(estimated_base / 1000.0) * 1000
                    traderData["day_base"] = snapped
                    # Lock it in once we have a clean reading (ts > 1000 for stability)
                    if timestamp > 1000:
                        traderData["day_base_locked"] = True

                day_base = traderData["day_base"] if traderData["day_base"] is not None else 10000

                # ── EXACT fair value (confirmed ±2 std accuracy) ───────
                fair = float(day_base) + timestamp * 0.001

                # ── Capacities ────────────────────────────────────────
                buy_cap  = LIMIT - position   # units we can still buy
                sell_cap = LIMIT + position   # units we can still sell (short side)

                # ── PHASE 1: AGGRESSIVE ENTRY — GET TO +20 FAST ───────
                # Cost of waiting: 0.1/tick × (units not yet held)
                # Cost of entering at ask: ~6-7 above fair
                # Break-even: 6/0.1 = 60 ticks — worth it IMMEDIATELY
                if best_ask is not None and buy_cap > 0:
                    ask_vol = abs(order_depth.sell_orders[best_ask])
                    # Enter aggressively up to ask + 8 above fair
                    # (beyond 8 = more than spread, price is dislocated)
                    if best_ask <= fair + 8:
                        qty = min(ask_vol, buy_cap, 10)
                        orders.append(Order(product, best_ask, qty))
                        buy_cap -= qty

                    # Also sweep all levels up to fair+8
                    for ask_px in sorted(order_depth.sell_orders.keys()):
                        if ask_px <= fair + 8 and buy_cap > 0:
                            qty = min(abs(order_depth.sell_orders[ask_px]), buy_cap, 5)
                            orders.append(Order(product, ask_px, qty))
                            buy_cap -= qty
                        elif ask_px > fair + 8:
                            break

                # ── PHASE 2: TREND RIDING — ONLY SELL WELL ABOVE FAIR ─
                # Don't fight the trend. Only sell if bid is GENUINELY above fair.
                # Threshold: fair + 8 (more than half-spread above fair)
                # This captures the oscillation upside without missing trend runs.
                if best_bid is not None and sell_cap > 0:
                    bid_vol = order_depth.buy_orders[best_bid]
                    if best_bid >= fair + 8 and position > 10:
                        # Only scalp from surplus position (keep min 10 long)
                        qty = min(bid_vol, sell_cap, 3)
                        orders.append(Order(product, best_bid, -qty))
                        sell_cap -= qty

                # ── PHASE 3: PASSIVE BUY — MAINTAIN POSITION ──────────
                # After aggressive entry, top up via passive orders.
                # Post buy at best_bid+1, capped at fair (don't overpay)
                if buy_cap > 0 and best_bid is not None and best_ask is not None:
                    passive_buy = min(best_bid + 1, int(fair), best_ask - 1)
                    if passive_buy > 0:
                        orders.append(Order(product, passive_buy, buy_cap))

                # ── PHASE 4: PASSIVE SELL — ONLY FOR POSITION CONTROL ─
                # If somehow we're short, post sell at a price that won't hurt
                if sell_cap > 0 and position > 0 and best_ask is not None:
                    passive_sell = max(best_ask - 1, int(fair) + 6)
                    if best_bid is not None:
                        passive_sell = max(passive_sell, best_bid + 1)
                    orders.append(Order(product, passive_sell, -sell_cap))

            # ==============================================================
            # 💎 ASH_COATED_OSMIUM — PRECISION MARKET MAKING
            # ==============================================================
            elif product == "ASH_COATED_OSMIUM":

                if best_bid is None and best_ask is None:
                    result[product] = orders
                    continue

                FAIR = 10000  # confirmed flat over all days

                # ── Inventory-adjusted fair ────────────────────────────
                # Skew: pull quotes toward fair when positioned.
                # Data: mid std is ±5, so 1.5/unit is firm but not over-aggressive.
                fair_adj = float(FAIR) - position * 1.5

                buy_cap  = LIMIT - position
                sell_cap = LIMIT + position

                # ── TIER 1: DEEP VALUE SWEEPS (highest priority) ───────
                # Data shows 181 trades below 9990 (avg 9986) over 3 days.
                # These are FREE MONEY: 10000-9986 = 14 per unit profit locked in.
                # We want EVERY unit we can get at these prices.
                if best_ask is not None and buy_cap > 0:
                    for ask_px in sorted(order_depth.sell_orders.keys()):
                        if ask_px <= FAIR - 5 and buy_cap > 0:
                            # Deep below fair — take maximum
                            qty = min(abs(order_depth.sell_orders[ask_px]), buy_cap, 10)
                            orders.append(Order(product, ask_px, qty))
                            buy_cap -= qty
                        elif ask_px > FAIR - 5:
                            break

                if best_bid is not None and sell_cap > 0:
                    for bid_px in sorted(order_depth.buy_orders.keys(), reverse=True):
                        if bid_px >= FAIR + 5 and sell_cap > 0:
                            # Deep above fair — take maximum
                            qty = min(order_depth.buy_orders[bid_px], sell_cap, 10)
                            orders.append(Order(product, bid_px, -qty))
                            sell_cap -= qty
                        elif bid_px < FAIR + 5:
                            break

                # ── TIER 2: STANDARD SWEEPS (mispricing ≥ 2) ──────────
                if best_ask is not None and buy_cap > 0:
                    for ask_px in sorted(order_depth.sell_orders.keys()):
                        if ask_px <= FAIR - 2 and buy_cap > 0:
                            qty = min(abs(order_depth.sell_orders[ask_px]), buy_cap, 5)
                            orders.append(Order(product, ask_px, qty))
                            buy_cap -= qty
                        elif ask_px > FAIR - 2:
                            break

                if best_bid is not None and sell_cap > 0:
                    for bid_px in sorted(order_depth.buy_orders.keys(), reverse=True):
                        if bid_px >= FAIR + 2 and sell_cap > 0:
                            qty = min(order_depth.buy_orders[bid_px], sell_cap, 5)
                            orders.append(Order(product, bid_px, -qty))
                            sell_cap -= qty
                        elif bid_px < FAIR + 2:
                            break

                # ── TIER 3: PASSIVE MM — CAPTURE THE 16-TICK SPREAD ───
                # Typical market: bid ~9992, ask ~10008 (spread=16)
                # We post: bid = best_bid+1, ask = best_ask-1
                # This gives us queue priority and captures spread of ~14.
                # Fill model: we get filled when the book sweeps past our level.
                if buy_cap > 0 and best_bid is not None and best_ask is not None:
                    passive_buy = min(
                        best_bid + 1,           # improve by 1 tick
                        int(fair_adj) - 1,      # must be below inventory-adj fair
                        best_ask - 1,           # never cross spread
                    )
                    if passive_buy > 0:
                        orders.append(Order(product, passive_buy, buy_cap))

                if sell_cap > 0 and best_ask is not None and best_bid is not None:
                    passive_sell = max(
                        best_ask - 1,           # improve by 1 tick
                        int(fair_adj) + 1,      # must be above inventory-adj fair
                        best_bid + 1,           # never cross spread
                    )
                    orders.append(Order(product, passive_sell, -sell_cap))

                # ── TIER 4: INVENTORY BLEED (prevent stuck positions) ──
                # If inventory is very high one side, reduce it at break-even.
                # This happens when one side fills but the other doesn't.
                if position > LIMIT - 3 and best_bid is not None:
                    # Over-long: sell a bit at market bid to rebalance
                    bleed_qty = min(3, position - (LIMIT - 5))
                    if bleed_qty > 0:
                        orders.append(Order(product, best_bid, -bleed_qty))

                if position < -(LIMIT - 3) and best_ask is not None:
                    # Over-short: buy a bit at market ask to rebalance
                    bleed_qty = min(3, (-position) - (LIMIT - 5))
                    if bleed_qty > 0:
                        orders.append(Order(product, best_ask, bleed_qty))

            result[product] = orders

        traderData = jsonpickle.encode(traderData)
        return result, 0, traderData