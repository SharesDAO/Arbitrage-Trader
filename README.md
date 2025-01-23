# SharesDAO Arbitrager
This program allows for arbitrage trading between XCH and the US stock market. Currently, the only supported trading strategy is DCA (Dollar-Cost Averaging). You will need to prepare some XCH before using this program. Feel free to expand upon this program, and please submit your pull request.


# How to run it
1. Prepare a **synced Chia wallet running locally** and has some XCH in it. Keep it open.

2. Install requirements: `pip install -r requirements.txt`

3. Prepare a DID in your Chia wallet. You can create a new one easily in the Chia wallet.

4. Register your DID on the [Shares DAO](https://www.sharesdao.com). Edit your trading strategy after login.

5 Run: `python main.py run -w <YOUR_CHIA_WALLET_FINGERPRINT> -d <YOUR_DID_IN_HEX> -s [DCA|Grid]` and keep the thread alive.

# Database and Logs
Your trading data will be saved in the trading_history.db and logs will in the trader.log

# How to update
## Trading Bot
Just checkout the latest code from Github or download the zip file and unzip to the same folder

## Trading Strategy
1. Login your account on the [Shares DAO](https://www.sharesdao.com) and edit your trading strategy there.
2. Restart your trading bot after saved the strategy.

# How to manually liquidate a stock
Run: `python main.py liquid -w <YOUR_CHIA_WALLET_FINGERPRINT> -d <YOUR_DID_IN_HEX> -t <Stock Ticker(e.g. GOOGL)> -s [DCA|Grid]`

# How to correct my positions
Run: `python main.py reset -t <Stock Ticker(e.g. GOOGL)> -v <ACTUAL_VOLUME> -w <YOUR_CHIA_WALLET_FINGERPRINT> -s [DCA|Grid]`
