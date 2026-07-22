# SharesDAO Arbitrager
This program allows for arbitrage trading between XCH and the US stock market. Currently, the only supported trading strategy is DCA (Dollar-Cost Averaging). You will need to prepare some XCH before using this program. Feel free to expand upon this program, and please submit your pull request.


# How to use it
Please check our [tutorial](https://www.sharesdao.com/trading-bot) for more information.

# Database and Logs
Your trading data will be saved in the trading_history.db and logs will in the trader.log

# How to update
## Trading Bot
Just checkout the latest code from Github or download the zip file and unzip to the same folder

## Trading Strategy
1. Login your account on the [Shares DAO](https://www.sharesdao.com) and edit your trading strategy there.
2. Restart your trading bot after saved the strategy.

# How to manually liquidate a stock
For a Solana fund, stop the bot and run: `python3 main.py liquid -d <YOUR_DID_IN_HEX> -t <Stock Ticker(e.g. GOOGL)> -s [DCA|Grid] -b SOLANA`

The command fetches the stock's live SPL-token balance through `SOLANA_RPC_URL`, submits one market sell for the full balance, and then deletes all DCA/Grid positions and trades for that ticker. In production, set `SOLANA_RPC_URL` to the Alchemy Solana endpoint.

For BSC or Arbitrum, set `EVM_PRIVATE_KEY` and the appropriate RPC URL, then run one of:

- `python3 main.py liquid -d <YOUR_DID_IN_HEX> -t <TICKER> -s Grid -b EVM -c bsc`
- `python3 main.py liquid -d <YOUR_DID_IN_HEX> -t <TICKER> -s Grid -b EVM -c arbitrum`

EVM liquidation reads the live ERC-20 balance from the configured RPC and uses `USDC` as the order memo currency.
