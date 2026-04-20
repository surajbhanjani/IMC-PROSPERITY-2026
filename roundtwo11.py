from datamodel import OrderDepth, TradingState, Order
from typing import List
import json

"""
╔══════════════════════════════════════════════════════════════════════════╗
║  FINAL PRO TRADER — Winning Team Strategies + Statistical Rigor        ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  INCORPORATED WINNING STRATEGIES:                                        ║
║  1. WALL-BASED MARKET MAKING (StaticTrader pattern)                     ║
║     → Post at bid+1/ask-1, let others cross into us                     ║
║  2. OVERBIDDING/UNDERBIDDING (volume>1 check)                           ║
║     → Compete only against real orders, ignore spoofs                   ║
║  3. DUAL THRESHOLDS (open vs close)                                      ║
║     → THR_OPEN=12 for entry, THR_CLOSE=8 for exit (hysteresis)         ║
║  4. RUNNING MEAN NORMALIZATION (EtfTrader EMA pattern)                  ║
║     → Track deviation from fair, normalize micro-drift                  ║
║  5. STATISTICAL GATEKEEPING (CommodityTrader confirmation)              ║
║     → Maintain signal history, only trade when mean confirms            ║
║                                                                          ║
║  TARGET: 10,000-12,000 PnL/day (top 10 leaderboard)                    ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

MAF_BID = 2000

class Trader:

    def run(self, state: TradingState):
        result = {}

        # ── Persistent State ────────────────────────────────────────────
        trader_data = {}
        if state.traderData:
            try:
                trader_data = json.loads(state.traderData)
            except:
                pass

        new_trader_data = {}
        timestamp = state.timestamp

        # ── Day rollover detection ──────────────────────────────────────
        prev_ts = trader_data.get("prev_ts", -1)
        if timestamp < prev_ts:
            # New day: reset everything
            trader_data = {}
        new_trader_data["prev_ts"] = timestamp

        for product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []

            # ── Extract order book walls ────────────────────────────────
            buy_orders = {p: abs(v) for p, v in sorted(order_depth.buy_orders.items(), reverse=True)}
            sell_orders = {p: abs(v) for p, v in sorted(order_depth.sell_orders.items())}

            best_bid = max(buy_orders.keys()) if buy_orders else None
            best_ask = min(sell_orders.keys()) if sell_orders else None

            # Walls = furthest bid/ask (like winning team's code)
            bid_wall = min(buy_orders.keys()) if buy_orders else None
            ask_wall = max(sell_orders.keys()) if sell_orders else None

            if best_bid and best_ask:
                wall_mid = (bid_wall + ask_wall) / 2 if (bid_wall and ask_wall) else (best_bid + best_ask) / 2
            else:
                wall_mid = None

            if wall_mid is None:
                result[product] = orders
                continue

            position = state.position.get(product, 0)
            LIMIT = 25  # MAF access target

            # ==========================================================
            # 🌿  PEPPER — WALL-BASED MARKET MAKING
            # ==========================================================
            if product == "INTARIAN_PEPPER_ROOT":

                # ── Infer fair value ─────────────────────────────────
                pepper_base = trader_data.get("pepper_base")
                if pepper_base is None or timestamp < 5000:
                    estimated = wall_mid - timestamp * 0.001
                    pepper_base = round(estimated / 1000.0) * 1000
                new_trader_data["pepper_base"] = pepper_base
                
                fair = pepper_base + timestamp * 0.001

                # ── Track running deviation (EtfTrader pattern) ──────
                deviation = wall_mid - fair
                old_mean_dev = trader_data.get("pepper_dev_ema", 0)
                alpha = 2 / (100 + 1)  # 100-tick EMA
                mean_dev = alpha * deviation + (1 - alpha) * old_mean_dev
                new_trader_data["pepper_dev_ema"] = mean_dev

                # Normalized deviation (current vs mean)
                norm_dev = deviation - mean_dev

                # ── Dual threshold logic (open vs close) ─────────────
                THR_OPEN = 12
                THR_CLOSE = 8

                buy_cap = LIMIT - position
                sell_cap = LIMIT + position

                # 1. TAKING (cross spread if far from fair)
                for sp, sv in sell_orders.items():
                    if sp <= fair - 1 and buy_cap > 0:  # Ask well below fair
                        qty = min(sv, buy_cap, LIMIT)
                        orders.append(Order(product, sp, qty))
                        buy_cap -= qty
                    elif sp <= fair and position < 0 and buy_cap > 0:
                        # Close short at fair
                        qty = min(sv, -position, buy_cap)
                        orders.append(Order(product, sp, qty))
                        buy_cap -= qty

                # Never sell pepper (trend is up)

                # 2. MAKING (post passive orders inside spread)
                if buy_cap > 0 and bid_wall and ask_wall:
                    
                    # Base case: bid at bid_wall + 1
                    our_bid = bid_wall + 1

                    # OVERBIDDING logic (only if volume > 1, like winning team)
                    for bp, bv in buy_orders.items():
                        if bv > 1 and bp + 1 < wall_mid and bp < fair:
                            our_bid = max(our_bid, bp + 1)
                            break
                        elif bp < wall_mid:
                            our_bid = max(our_bid, bp)
                            break

                    # Don't bid above fair threshold
                    if our_bid <= fair + THR_OPEN and buy_cap > 0:
                        orders.append(Order(product, our_bid, min(buy_cap, 10)))

                    # Also sweep asks within threshold if still need position
                    buy_cap = LIMIT - position - sum(o.quantity for o in orders if o.quantity > 0)
                    if buy_cap > 0:
                        for sp in sorted(sell_orders.keys()):
                            if sp <= fair + THR_OPEN and buy_cap > 0:
                                qty = min(sell_orders[sp], buy_cap, LIMIT)
                                orders.append(Order(product, sp, qty))
                                buy_cap -= qty
                            elif sp > fair + THR_OPEN:
                                break

            # ==========================================================
            # 💎  OSMIUM — QUALITY SIGNAL WITH STATISTICAL GATING
            # ==========================================================
            elif product == "ASH_COATED_OSMIUM":

                # ── EMA tracking (like winning team's OptionTrader) ──
                old_ema = trader_data.get("osmium_ema", 10000)
                alpha_ema = 0.015
                ema = (1 - alpha_ema) * old_ema + alpha_ema * wall_mid
                new_trader_data["osmium_ema"] = ema

                spread = best_ask - best_bid if (best_ask and best_bid) else 999

                buy_cap = LIMIT - position
                sell_cap = LIMIT + position

                # ── Quality buy signal with statistical confirmation ─
                quality_buy_signal = (
                    best_ask and best_ask <= 9998 and
                    best_bid and best_bid >= 9990 and
                    spread <= 7
                )

                # Maintain signal history (CommodityTrader pattern)
                buy_signals = trader_data.get("osm_buy_signals", [])
                if len(buy_signals) > 10:
                    buy_signals.pop(0)
                buy_signals.append(1 if quality_buy_signal else 0)
                new_trader_data["osm_buy_signals"] = buy_signals

                mean_buy_signal = sum(buy_signals) / len(buy_signals) if buy_signals else 0

                # Only trade if BOTH current signal AND mean signal confirm
                if quality_buy_signal and mean_buy_signal > 0.3 and buy_cap > 0:
                    
                    # 1. TAKING: aggressive at best ask
                    if spread <= 5:  # Very tight spread = high confidence
                        qty = min(sell_orders[best_ask], buy_cap, 5)
                        orders.append(Order(product, best_ask, qty))
                        buy_cap -= qty

                    # 2. MAKING: post at 9995 (mid of quality zone)
                    if buy_cap > 0:
                        our_bid = 9995
                        if our_bid > best_bid and our_bid < best_ask:
                            orders.append(Order(product, our_bid, min(buy_cap, 5)))

                # ── Quality sell signal ───────────────────────────────
                quality_sell_signal = (
                    best_bid and best_bid >= 10001 and
                    position > 0
                )

                if quality_sell_signal and sell_cap > 0:
                    
                    # 1. TAKING: aggressive at best bid
                    qty = min(buy_orders[best_bid], sell_cap, position, 5)
                    orders.append(Order(product, best_bid, -qty))
                    sell_cap -= qty

                    # 2. MAKING: post at 10005
                    sell_cap = LIMIT + position - sum(-o.quantity for o in orders if o.quantity < 0)
                    if sell_cap > 0 and position > 0:
                        our_ask = 10005
                        if our_ask > best_bid and our_ask < best_ask:
                            orders.append(Order(product, our_ask, -min(sell_cap, position, 5)))

                # ── Safety: inventory bleed ───────────────────────────
                if position > LIMIT - 3 and best_bid:
                    qty = min(2, position - (LIMIT - 4))
                    if qty > 0:
                        orders.append(Order(product, best_bid, -qty))

                # Never hold short
                if position < 0 and best_ask:
                    orders.append(Order(product, best_ask, min(3, -position)))

            result[product] = orders

        # ── Save state ──────────────────────────────────────────────────
        trader_data_str = ""
        try:
            trader_data_str = json.dumps(new_trader_data)
        except:
            pass

        return result, MAF_BID, trader_data_str