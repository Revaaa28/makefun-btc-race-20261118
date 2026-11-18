"""Hybrid momentum + deep-dip bot for the make.fun cbBTC bars-replay
race (agent-fun/bars-replay/v1).

Design (long only, at most one position, paper USDC). Two entry modules
scan every bar; whichever fires first takes the position, and each
module's exits match its own logic:

  * Trend module (the active core). An EMA 12/48 cross-up on 1-minute
    closes arms an entry only when the move is already real: the close
    must sit above the slow EMA and the trailing 120-bar return must be
    at least +40 bps — comfortably more than the ~26 bps a round trip
    costs in fees, slippage and impact, so chop crosses (the 8/21 pair
    crosses ~80 times a day in this market) never get chased. A trend
    position is protected by a -75 bps hard stop and a trailing stop
    (arms at +50 bps, exits 60 bps under the peak close) so a real move
    is ridden but not handed back, plus a 480-bar time stop.

  * Dip module (the proven deep-dip trigger, as an additional entry).
    When the close sits at least 200 bps below the trailing 60-bar high
    of bar highs, the latest bar bounced (close above the previous
    close) and Wilder RSI(14) is at or under 35, buy the washout. A dip
    position gets room to recover: a -60 bps hard stop and a 420-bar
    time stop, and deliberately no trailing stop — on the dev window a
    trailing stop cut this exact trade from +$14.95 to +$3.17 by
    shaking it out on the first pullback of the recovery.

  * A 10-bar cooldown follows every exit. Entries are all-in.

Why this shape: the dev window (24h ending 2026-11-18T14:44Z) is a
downtrend — buy and hold loses 3.16% after costs, the range is only
3.35%, and the harvestable upside is one washout to ~$63.6k followed by
a slow relief rally. Every loose trend variant tested on it bled to
the round-trip cost (state-based EMA entries: -$27 to -$62); only
cross entries with a strong momentum confirmation stayed near
breakeven, and the dip-and-hold trade carried the window. The chosen
parameters sit mid-plateau: varying any one of them (dip 180-220 bps,
dip stop 50-75, momentum +30..+50 bps over 90-150 bars, trailing
45-75 bps) keeps the dev result between +$6.6 and +$15.0.

Measured on the kit's dev window with a fill-faithful simulator (the
harness's own fill math, integer for integer; it reproduces the
grader's buy-and-hold score of -31.633698 USDC exactly): net PnL
+10.670045 USDC, 2 round trips (dip +14.946576, trend -4.276531),
win rate 50%, profit factor 3.50, expectancy +$5.34 per trade, max
drawdown 15.133274 USDC (1.51% of the starting cash). Truncated
half-window runs: first half +14.946576 (the dip trade), second half
-11.195341 (one trend entry stopped out in the bleed; the dip module
never fired — a slow grind down makes no 200 bps washout). The second
half is this design's weak side and is reported as such. The shipped
file was also run end-to-end over stdio under the harness's own
runtime command (python3 -P -s, PYTHONHASHSEED=0): two runs, reply
streams byte-identical, same +10.670045 USDC.

Deterministic: no wall clock, no randomness, no environment reads;
the only inputs are the hello and the batches. All arithmetic is
integer, identical every run.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars  # noqa: E402

SYMBOL = "CBBTC"

PARAMS = {
    "ema_fast": 12,       # trend module: fast EMA of closes
    "ema_slow": 48,       # trend module: slow EMA of closes
    "mom_win": 120,       # momentum confirmation window (bars)
    "mom_bps": 40,        # min trailing return for a trend entry
    "dip_win": 60,        # dip module: trailing-high window (bars)
    "dip_bps": 200,       # dip module: depth below the trailing high
    "rsi_period": 14,     # Wilder RSI period
    "rsi_max": 35,        # dip entries need RSI at or below this
    "trend_stop_bps": 75,   # hard stop, trend entries
    "dip_stop_bps": 60,     # hard stop, dip entries
    "trail_arm_bps": 50,    # trailing stop (trend entries only): arm
    "trail_bps": 60,        # trailing stop: exit below the peak close
    "trend_time_stop": 480,  # max bars held, trend entries
    "dip_time_stop": 420,    # max bars held, dip entries
    "cooldown": 10,       # bars after an exit before re-entering
    "size_pct": 100,      # percent of cash per entry
    "buy_slip_bps": 10000,   # max_slippage_bps on buys (never refuses)
    "sell_slip_bps": 10000,  # max_slippage_bps on sells
}


class Bot:
    def __init__(self, hello):
        self.p = dict(PARAMS)
        sym = hello["symbols"][0]
        self.qty_scale = sym["qty_scale"]
        self.price_scale = sym["price_scale"]
        self.quote_scale = hello["capital"]["quote_scale"]
        self.min_notional = int(hello["fees"]["min_notional_quote_units"])
        self.closes = []          # close of every bar seen, in order
        self.highs = []           # high of every bar seen, in order
        self.ema_f = None         # fast EMA of closes (price units)
        self.ema_s = None         # slow EMA of closes
        self.cross_up = False     # fast crossed above slow on this bar
        self.avg_gain = 0         # Wilder RSI state, scaled by 10**6
        self.avg_loss = 0
        self.rsi_bars = 0
        self.entry_price = 0      # close of the bar that decided the buy
        self.entry_seq = -1       # bar index that decided the buy
        self.entry_kind = None    # "trend" or "dip"
        self.peak_close = 0       # highest close since the entry
        self.last_exit = -10 ** 9  # bar index of the last sell decision
        self.pending = None       # "buy" / "sell" awaiting its fill
        self.order_n = 0

    def _next_id(self, tag):
        self.order_n += 1
        return "%s%d" % (tag, self.order_n)

    def _update_indicators(self, close, prev_close):
        p = self.p
        prev_f, prev_s = self.ema_f, self.ema_s
        if self.ema_f is None:
            self.ema_f = close
            self.ema_s = close
        else:
            self.ema_f += (close - self.ema_f) * 2 // (p["ema_fast"] + 1)
            self.ema_s += (close - self.ema_s) * 2 // (p["ema_slow"] + 1)
        self.cross_up = (prev_f is not None and prev_f <= prev_s
                         and self.ema_f > self.ema_s)
        if prev_close is not None:
            chg = close - prev_close
            gain = chg if chg > 0 else 0
            loss = -chg if chg < 0 else 0
            n = p["rsi_period"]
            if self.rsi_bars < n:
                self.avg_gain += gain * 10 ** 6 // n
                self.avg_loss += loss * 10 ** 6 // n
            else:
                self.avg_gain = (self.avg_gain * (n - 1)
                                 + gain * 10 ** 6) // n
                self.avg_loss = (self.avg_loss * (n - 1)
                                 + loss * 10 ** 6) // n
            self.rsi_bars += 1

    def _rsi(self):
        total = self.avg_gain + self.avg_loss
        if total == 0 or self.rsi_bars < self.p["rsi_period"]:
            return 100
        return 100 * self.avg_gain // total

    def on_batch(self, state, batch):
        prev_close = self.closes[-1] if self.closes else None
        for bar in batch["bars"]:
            if bar["symbol"] == SYMBOL:
                self.closes.append(int(bar["c"]))
                self.highs.append(int(bar["h"]))
        # Any resolution of the pending order (filled or rejected)
        # clears the flag; the account in `state` is the truth about
        # whether a position exists.
        for fill in batch["fills"]:
            if self.pending == "buy" and fill["side"] == "buy":
                self.pending = None
            elif self.pending == "sell" and fill["side"] == "sell":
                self.pending = None
        if state.warming_up or not self.closes:
            return None
        close = self.closes[-1]
        self._update_indicators(close, prev_close)
        i = len(self.closes) - 1
        p = self.p
        holding = state.positions.get(SYMBOL)

        if holding is not None and self.pending is None:
            if close > self.peak_close:
                self.peak_close = close
            gain = (close - self.entry_price) * 10000 // self.entry_price
            if self.entry_kind == "dip":
                stop = p["dip_stop_bps"]
                tstop = p["dip_time_stop"]
            else:
                stop = p["trend_stop_bps"]
                tstop = p["trend_time_stop"]
            exit_now = False
            if gain <= -stop:
                exit_now = True                       # hard stop
            elif (self.entry_kind == "trend"
                  and self.peak_close > self.entry_price
                  and (self.peak_close - self.entry_price) * 10000
                      // self.entry_price >= p["trail_arm_bps"]
                  and close * 10000
                      <= self.peak_close * (10000 - p["trail_bps"])):
                exit_now = True                       # trailing stop
            elif i - self.entry_seq >= tstop:
                exit_now = True                       # time stop
            if exit_now:
                self.pending = "sell"
                self.last_exit = i
                return [{
                    "side": "sell",
                    "client_id": self._next_id("s"),
                    "symbol": SYMBOL,
                    "qty": holding.qty,
                    "max_slippage_bps": p["sell_slip_bps"],
                }]
            return None

        if holding is not None or self.pending is not None:
            return None
        if i - self.last_exit < p["cooldown"]:
            return None
        # Warm-up is per module: the dip module must not wait on the
        # trend module's long momentum window, nor the reverse.
        trend_ready = i >= max(p["ema_slow"], p["mom_win"])
        dip_ready = i >= max(p["dip_win"], p["rsi_period"] + 1)
        if not trend_ready and not dip_ready:
            return None

        kind = None
        # Trend module: an EMA cross-up with the move already real —
        # close above the slow EMA and a trailing 120-bar return that
        # clears the round-trip cost with room to spare.
        if (trend_ready and self.cross_up
                and self.ema_f > self.ema_s and close > self.ema_s):
            base = self.closes[i - p["mom_win"]]
            mom = (close - base) * 10000 // base
            if mom >= p["mom_bps"]:
                kind = "trend"
        # Dip module: a deep washout off the trailing high, already
        # bouncing, with RSI oversold.
        if kind is None and dip_ready:
            win = self.highs[i - p["dip_win"]:i]
            hi = max(win)
            if (close * 10000 <= hi * (10000 - p["dip_bps"])
                    and close > self.closes[i - 1]
                    and self._rsi() <= p["rsi_max"]):
                kind = "dip"
        if kind is None:
            return None

        notional = state.cash * p["size_pct"] // 100
        room = state.weight_room(SYMBOL)
        if room < notional:
            notional = room
        if notional < self.min_notional:
            return None
        self.pending = "buy"
        self.entry_price = close
        self.entry_seq = i
        self.entry_kind = kind
        self.peak_close = close
        return [{
            "side": "buy",
            "client_id": self._next_id("b"),
            "symbol": SYMBOL,
            "notional_quote_units": notional,
            "max_slippage_bps": p["buy_slip_bps"],
        }]


def main():
    holder = {}

    def on_batch(state, batch):
        bot = holder.get("bot")
        if bot is None:
            bot = holder["bot"] = Bot(state.hello)
        return bot.on_batch(state, batch)

    makefun_bars.run(on_batch)


if __name__ == "__main__":
    main()
