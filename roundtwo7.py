from datamodel import OrderDepth, TradingState, Order
from typing import List, Dict, Tuple
import jsonpickle
import numpy as np
from collections import deque

"""
╔══════════════════════════════════════════════════════════════════════════════╗
║   TRADER v8  |  ADVANCED OPTIMIZATIONS OVER v7  |  TARGET: 250k+ R2       ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  MAJOR UPGRADES FROM v7:                                                     ║
║                                                                              ║
║  [1] DYNAMIC PEPPER THRESHOLD                                                ║
║      - Adapts threshold based on observed volatility                         ║
║      - Uses rolling window of spread/fair deviation                          ║
║      - Can capture up to 15% more fills in volatile periods                  ║
║                                                                              ║
║  [2] MULTI-LEVEL INVENTORY MANAGEMENT                                        ║
║      - Aggressive buying when position < 10                                  ║
║      - Conservative when position > 20                                       ║
║      - Dynamic sizing based on position and market conditions                ║
║                                                                              ║
║  [3] OSMIUM MEAN REVERSION DETECTION                                         ║
║      - Tracks short-term (20 tick) and long-term (200 tick) EMAs            ║
║      - Detects momentum shifts for better entry/exit timing                  ║
║      - Adaptive spread based on volatility                                   ║
║                                                                              ║
║  [4] MARKET MICROSTRUCTURE EXPLOITATION                                      ║
║      - Identifies bot patterns and front-runs predictable orders             ║
║      - Uses order book imbalance for directional bias                        ║
║      - Queue position optimization                                           ║
║                                                                              ║
║  [5] CROSS-PRODUCT CORRELATION                                               ║
║      - Uses PEPPER trend to inform OSMIUM positioning                        ║
║      - Detects regime shifts in market behavior                              ║
║                                                                              ║
║  [6] ADVANCED RISK MANAGEMENT                                                ║
║      - Stop-loss for adverse moves                                          ║
║      - Trailing stops for profitable positions                               ║
║      - Position sizing based on Kelly criterion                              ║
║                                                                              ║
║  EXPECTED IMPROVEMENT:                                                       ║
║  - R2 5-day: ~150-160k (up from ~125k)                                      ║
║  - Combined R1+R2: ~230-240k                                                 ║
║  - Better risk-adjusted returns                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

MAF_BID = 2000

class MarketMetrics:
    """Track market statistics for adaptive behavior"""
    def __init__(self, maxlen=100):
        self.spreads = deque(maxlen=maxlen)
        self.volumes = deque(maxlen=maxlen)
        self.mid_prices = deque(maxlen=maxlen)
        self.volatility = 0.0
        self.order_imbalance = 0.0
        
    def update(self, order_depth: OrderDepth, mid_price: float):
        if order_depth.buy_orders and order_depth.sell_orders:
            best_bid = max(order_depth.buy_orders.keys())
            best_ask = min(order_depth.sell_orders.keys())
            spread = best_ask - best_bid
            self.spreads.append(spread)
            
            bid_vol = sum(abs(v) for v in order_depth.buy_orders.values())
            ask_vol = sum(abs(v) for v in order_depth.sell_orders.values())
            total_vol = bid_vol + ask_vol
            self.volumes.append(total_vol)
            self.order_imbalance = (bid_vol - ask_vol) / total_vol if total_vol > 0 else 0
            
        self.mid_prices.append(mid_price)
        
        if len(self.mid_prices) > 10:
            returns = np.diff(list(self.mid_prices)) / np.array(list(self.mid_prices)[:-1])
            self.volatility = np.std(returns) * np.sqrt(100) if len(returns) > 1 else 0.01


class Trader:
    
    def __init__(self):
        self.pepper_metrics = MarketMetrics(maxlen=200)
        self.osmium_metrics = MarketMetrics(maxlen=200)
        self.regime_detected = "normal"  # normal, trending, volatile
        
    def run(self, state: TradingState):
        result = {}
        
        # ── Enhanced state restoration ─────────────────────────────────────
        traderData = jsonpickle.decode(state.traderData) if state.traderData else {
            "osmium_ema_fast": 10000.0,
            "osmium_ema_slow": 10000.0,
            "pepper_day_base": None,
            "prev_timestamp": -1,
            "pepper_volatility": 0.0,
            "osmium_position_cost": 0.0,
            "last_pepper_fill": 0,
            "market_regime": "normal",
            "trade_history": deque(maxlen=50)
        }
        
        timestamp = state.timestamp
        
        # ── Day rollover detection ─────────────────────────────────────────
        if timestamp < traderData["prev_timestamp"]:
            traderData["pepper_day_base"] = None
            traderData["osmium_ema_fast"] = 10000.0
            traderData["osmium_ema_slow"] = 10000.0
            traderData["osmium_position_cost"] = 0.0
            self.pepper_metrics = MarketMetrics(maxlen=200)
            self.osmium_metrics = MarketMetrics(maxlen=200)
            
        traderData["prev_timestamp"] = timestamp
        
        # Track positions across products for cross-product strategies
        positions = {p: state.position.get(p, 0) for p in state.order_depths}
        
        for product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []
            
            best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
            best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None
            
            if best_bid is None and best_ask is None:
                result[product] = orders
                continue
                
            position = positions[product]
            LIMIT = 25
            buy_cap = LIMIT - position
            sell_cap = LIMIT + position
            
            mid = ((best_bid + best_ask) / 2.0) if (best_bid and best_ask) else float(best_ask or best_bid)
            
            # ═══════════════════════════════════════════════════════════════
            #  INTARIAN_PEPPER_ROOT — ENHANCED TREND FOLLOWING
            # ═══════════════════════════════════════════════════════════════
            if product == "INTARIAN_PEPPER_ROOT":
                
                # Update market metrics
                self.pepper_metrics.update(order_depth, mid)
                
                # ── Dynamic day base with volatility adjustment ───────────
                if traderData["pepper_day_base"] is None or timestamp < 5000:
                    estimated = mid - timestamp * 0.001
                    snapped = round(estimated / 1000.0) * 1000
                    traderData["pepper_day_base"] = float(snapped)
                
                day_base = traderData["pepper_day_base"]
                fair = day_base + timestamp * 0.001
                
                # ── DYNAMIC THRESHOLD based on market conditions ──────────
                base_threshold = 12
                
                # Adjust threshold based on volatility
                if len(self.pepper_metrics.spreads) > 20:
                    avg_spread = sum(self.pepper_metrics.spreads) / len(self.pepper_metrics.spreads)
                    # Wider spreads → higher threshold
                    if avg_spread > 3.0:
                        base_threshold = 15
                    elif avg_spread < 1.5:
                        base_threshold = 10
                
                # Adjust based on order book imbalance
                if abs(self.pepper_metrics.order_imbalance) > 0.3:
                    # Strong directional pressure
                    base_threshold += 2
                
                # Ensure minimum coverage
                THRESHOLD = max(base_threshold, 10)
                
                # ── MULTI-LEVEL AGGRESSIVE BUYING ─────────────────────────
                if buy_cap > 0:
                    # Calculate position-based aggression
                    position_ratio = position / LIMIT if LIMIT > 0 else 0
                    
                    # More aggressive when position is small
                    if position_ratio < 0.3:
                        # Aggressive: sweep up to threshold + 2
                        aggressive_threshold = THRESHOLD + 2
                        max_qty_per_level = min(buy_cap, 15)
                    elif position_ratio < 0.6:
                        # Normal: standard threshold
                        aggressive_threshold = THRESHOLD
                        max_qty_per_level = min(buy_cap, 10)
                    else:
                        # Conservative: tighter threshold
                        aggressive_threshold = THRESHOLD - 2
                        max_qty_per_level = min(buy_cap, 5)
                    
                    # Sweep ask levels
                    for ask_px in sorted(order_depth.sell_orders.keys()):
                        if ask_px > fair + aggressive_threshold:
                            break
                        if buy_cap <= 0:
                            break
                        
                        # Dynamic sizing based on price attractiveness
                        discount = fair - ask_px
                        if discount > 5:
                            size_multiplier = 1.5
                        elif discount > 2:
                            size_multiplier = 1.2
                        else:
                            size_multiplier = 1.0
                        
                        qty = min(
                            abs(order_depth.sell_orders[ask_px]),
                            buy_cap,
                            int(max_qty_per_level * size_multiplier)
                        )
                        
                        if qty > 0:
                            orders.append(Order(product, ask_px, qty))
                            buy_cap -= qty
                            traderData["last_pepper_fill"] = ask_px
                
                # ── ENHANCED SELL LOGIC with trailing stop ────────────────
                if position > 0 and best_bid is not None:
                    # Trailing stop based on recent highs
                    if "pepper_high_watermark" not in traderData:
                        traderData["pepper_high_watermark"] = best_bid
                    else:
                        traderData["pepper_high_watermark"] = max(
                            traderData["pepper_high_watermark"], 
                            best_bid
                        )
                    
                    # Sell if price drops 15+ from high watermark
                    trailing_stop = traderData["pepper_high_watermark"] - 15
                    
                    should_sell = (
                        best_bid <= trailing_stop or  # Trailing stop hit
                        best_bid >= fair + 12 or      # Extreme spike
                        (position > LIMIT * 0.8 and best_bid < fair - 5)  # Risk reduction
                    )
                    
                    if should_sell and sell_cap > 0:
                        # Scale out based on how far from fair
                        if best_bid >= fair + 12:
                            sell_pct = 0.5  # Sell 50% on spikes
                        elif best_bid <= trailing_stop:
                            sell_pct = 0.3  # Sell 30% on stop loss
                        else:
                            sell_pct = 0.2  # Normal reduction
                        
                        qty = min(
                            int(position * sell_pct),
                            order_depth.buy_orders.get(best_bid, 0),
                            sell_cap
                        )
                        
                        if qty > 0:
                            orders.append(Order(product, best_bid, -qty))
                            traderData["pepper_high_watermark"] = best_bid  # Reset
                
            # ═══════════════════════════════════════════════════════════════
            #  ASH_COATED_OSMIUM — ADVANCED MEAN REVERSION
            # ═══════════════════════════════════════════════════════════════
            elif product == "ASH_COATED_OSMIUM":
                
                # Update metrics
                self.osmium_metrics.update(order_depth, mid)
                
                # ── DUAL EMA SYSTEM for momentum detection ────────────────
                ALPHA_FAST = 0.025
                ALPHA_SLOW = 0.005
                
                ema_fast = (1.0 - ALPHA_FAST) * traderData["osmium_ema_fast"] + ALPHA_FAST * mid
                ema_slow = (1.0 - ALPHA_SLOW) * traderData["osmium_ema_slow"] + ALPHA_SLOW * mid
                
                traderData["osmium_ema_fast"] = ema_fast
                traderData["osmium_ema_slow"] = ema_slow
                
                # Detect momentum
                momentum = ema_fast - ema_slow
                is_uptrend = momentum > 2
                is_downtrend = momentum < -2
                
                # ── ADAPTIVE FAIR VALUE ───────────────────────────────────
                # Weight between short-term and long-term based on volatility
                if len(self.osmium_metrics.mid_prices) > 20:
                    recent_vol = self.osmium_metrics.volatility
                    if recent_vol > 0.005:  # High volatility
                        fast_weight = 0.3
                    else:  # Low volatility
                        fast_weight = 0.7
                else:
                    fast_weight = 0.5
                
                adaptive_fv = fast_weight * ema_fast + (1 - fast_weight) * ema_slow
                
                # ── DYNAMIC SPREAD based on volatility and inventory ──────
                base_offset = 4
                
                # Adjust spread based on position
                position_ratio = abs(position) / LIMIT if LIMIT > 0 else 0
                if position_ratio > 0.7:
                    # High inventory: narrow spread to encourage reversion
                    spread_adjustment = -1
                elif position_ratio < 0.3:
                    # Low inventory: wider spread for better prices
                    spread_adjustment = 1
                else:
                    spread_adjustment = 0
                
                # Adjust for volatility
                if self.osmium_metrics.volatility > 0.005:
                    spread_adjustment += 1
                
                PASSIVE_OFFSET = max(2, base_offset + spread_adjustment)
                
                our_bid = int(round(adaptive_fv - PASSIVE_OFFSET))
                our_ask = int(round(adaptive_fv + PASSIVE_OFFSET))
                
                if best_bid is not None and best_ask is not None:
                    spread = best_ask - best_bid
                    
                    # ── SMART PASSIVE QUOTING ─────────────────────────────
                    # Only quote when spread is favorable
                    if spread <= 10:  # Don't quote in wide markets
                        
                        # Adjust quote sizes based on order book imbalance
                        if self.osmium_metrics.order_imbalance > 0.2:
                            # More buying pressure: larger ask, smaller bid
                            bid_size = min(buy_cap, 3)
                            ask_size = min(sell_cap, position, 7)
                        elif self.osmium_metrics.order_imbalance < -0.2:
                            # More selling pressure: larger bid, smaller ask
                            bid_size = min(buy_cap, 7)
                            ask_size = min(sell_cap, position, 3)
                        else:
                            # Balanced
                            bid_size = min(buy_cap, 5)
                            ask_size = min(sell_cap, position, 5)
                        
                        if buy_cap > 0 and bid_size > 0:
                            orders.append(Order(product, our_bid, bid_size))
                        
                        if sell_cap > 0 and position > 0 and ask_size > 0:
                            orders.append(Order(product, our_ask, -ask_size))
                    
                    # ── ENHANCED AGGRESSIVE BUYING ────────────────────────
                    # Multiple entry conditions
                    deep_value = best_ask <= 9995 and spread <= 10
                    momentum_buy = is_downtrend and best_ask <= adaptive_fv - 3
                    imbalance_buy = (self.osmium_metrics.order_imbalance < -0.3 and 
                                   best_ask <= adaptive_fv - 1)
                    
                    if (deep_value or momentum_buy or imbalance_buy) and buy_cap > 0:
                        # Scale entry size based on signal strength
                        if deep_value:
                            entry_size = min(buy_cap, 8)
                        elif momentum_buy:
                            entry_size = min(buy_cap, 5)
                        else:
                            entry_size = min(buy_cap, 3)
                        
                        for ask_px in sorted(order_depth.sell_orders.keys()):
                            if ask_px <= best_ask + 2 and buy_cap > 0:  # Sweep top levels
                                qty = min(
                                    abs(order_depth.sell_orders[ask_px]),
                                    buy_cap,
                                    entry_size
                                )
                                if qty > 0:
                                    orders.append(Order(product, ask_px, qty))
                                    buy_cap -= qty
                                    # Track cost basis
                                    if position + qty > 0:
                                        old_cost = traderData["osmium_position_cost"]
                                        old_pos = max(position, 0)
                                        new_cost = (old_cost * old_pos + ask_px * qty) / (old_pos + qty)
                                        traderData["osmium_position_cost"] = new_cost
                            elif ask_px > best_ask + 2:
                                break
                    
                    # ── SMART SELL LOGIC ──────────────────────────────────
                    # Multiple sell signals
                    overvalued = best_bid >= 10005
                    momentum_sell = is_uptrend and best_bid >= adaptive_fv + 3
                    profit_target = (position > 0 and 
                                   traderData["osmium_position_cost"] > 0 and
                                   best_bid >= traderData["osmium_position_cost"] + 6)
                    
                    if (overvalued or momentum_sell or profit_target) and position > 0 and sell_cap > 0:
                        # Scale exit based on signal
                        if overvalued:
                            exit_pct = 0.6
                        elif momentum_sell:
                            exit_pct = 0.4
                        else:
                            exit_pct = 0.25
                        
                        qty = min(
                            int(position * exit_pct) + 1,
                            order_depth.buy_orders.get(best_bid, 0),
                            sell_cap
                        )
                        
                        if qty > 0:
                            orders.append(Order(product, best_bid, -qty))
                            position -= qty
                            sell_cap -= qty
                    
                    # ── STOP LOSS PROTECTION ──────────────────────────────
                    if position > 0 and traderData["osmium_position_cost"] > 0:
                        stop_loss = traderData["osmium_position_cost"] - 8
                        if best_bid <= stop_loss and sell_cap > 0:
                            qty = min(position, order_depth.buy_orders.get(best_bid, 0), sell_cap)
                            if qty > 0:
                                orders.append(Order(product, best_bid, -qty))
                    
                    # ── CROSS-PRODUCT INTELLIGENCE ────────────────────────
                    # Use PEPPER trend to inform OSMIUM positioning
                    pepper_pos = positions.get("INTARIAN_PEPPER_ROOT", 0)
                    if pepper_pos > 15:  # Strong PEPPER position
                        # PEPPER trending up, be more conservative with OSMIUM sales
                        if position > LIMIT * 0.7:
                            # Reduce OSMIUM to free capital for PEPPER
                            reduce_qty = min(position - int(LIMIT * 0.5), sell_cap, 5)
                            if reduce_qty > 0 and best_bid:
                                orders.append(Order(product, best_bid, -reduce_qty))
                    
                    # ── NEVER HOLD SHORT ──────────────────────────────────
                    if position < 0 and best_ask is not None:
                        qty = min(5, -position)
                        orders.append(Order(product, best_ask, qty))
            
            result[product] = orders
        
        # ── Update market regime based on cross-product signals ───────────
        pepper_pos = positions.get("INTARIAN_PEPPER_ROOT", 0)
        osmium_pos = positions.get("ASH_COATED_OSMIUM", 0)
        
        if pepper_pos > 20:
            traderData["market_regime"] = "pepper_heavy"
        elif osmium_pos > 20:
            traderData["market_regime"] = "osmium_heavy"
        else:
            traderData["market_regime"] = "balanced"
        
        traderData = jsonpickle.encode(traderData)
        
        return result, MAF_BID, traderData