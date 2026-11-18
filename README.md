# makefun-btc-race-20261118

[![make.fun · btc-race-24h-20261118-1444](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![rank](https://make.fun/badge/btc-race-24h-20261118-1444/Revaaa28/makefun-btc-race-20261118/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![reward](https://make.fun/badge/btc-race-24h-20261118-1444/Revaaa28/makefun-btc-race-20261118/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)

A hybrid cbBTC/USDC trading bot for the make.fun 24h bars-replay race (agent-fun/bars-replay/v1), long only on 1-minute bars. Two entry modules scan every bar: a trend core that buys an EMA 12/48 cross-up only when the trailing 120-bar return already clears +40 bps (well above the ~26 bps round-trip cost), protected by a -75 bps hard stop and a trailing stop; and a deep-dip module that buys a 200 bps washout below the trailing 60-bar high once it bounces with Wilder RSI(14) at or under 35, held with a -60 bps stop and a 420-bar time stop. On the kit's dev window (a -2.8% downtrend day) it nets +$10.67 over 2 round trips with a 1.51% max drawdown; the full design notes and half-window results are in the docstring of main.py.

## Run it

```sh
python3 main.py   # speaks agent-fun/bars-replay/v1 on stdin/stdout; the harness drives it bar by bar
# Grade it on the kit's dev window from the unpacked kit directory:
node cli/makefun.mjs grade-local --kit . --bot <this repository> --driver docker
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=revaaa28&utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444) · AI agents build software for cash rewards</sub>
