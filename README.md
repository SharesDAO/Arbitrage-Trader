# SharesDAO Arbitrager
This program allows for arbitrage trading between XCH and the US stock market. Currently, the only supported trading strategy is DCA (Dollar-Cost Averaging). You will need to prepare some XCH before using this program. Feel free to expand upon this program, and please submit your pull request.

# How to use it
1 Prepare a synced Chia wallet running locally and has some XCH in it.

2 Install requirements: `pip install -r requirements.txt`

3 Change the constant.py file based on your case. Values you must change:
  - DID_HEX: Your DID hex value and it must be registered on SharesDAO
  - CHIA_PATH: Your Chia wallet binary file path
  - BUY_VOLUME: The amount of XCH you want to spend on each buy
  - WALLET_FINGERPRINT: Your wallet fingerprint
  - INVESTED_XCH: Your invested XCH, for calculate profit purpose only.
  - TRADING_SYMBOLS: Which stocks you want to trade. They must be listed on the SharesDAO.

4 Run: `python main.py` and keep the thread alive.

# Database and Logs
Your trading data will be saved in the trading_history.db and logs will in the trader.log