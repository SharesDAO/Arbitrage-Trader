from datetime import datetime

import requests
from cachetools import TTLCache, cached

cache = TTLCache(maxsize=100, ttl=60)
clock = TTLCache(maxsize=1, ttl=60)


def fetch_token_infos():
    url = "https://api.sbt.dinari.com/api/v1/chain/42161/token_infos"
    response = requests.get(url)

    if response.status_code == 200:
        return response.json()
    else:
        response.raise_for_status()


# Get stock info
stock_ids = {}
token_infos = fetch_token_infos()
for token_info in token_infos:
    stock = token_info.get('stock', {})
    stock_id = stock.get('id')
    stock_ids[stock.get('symbol')] = stock_id


@cached(clock)
def is_market_open(logger) -> bool:
    url = "https://www.sharesdao.com:8443/util/market_status"
    response = requests.get(url)

    if response.status_code == 200:
        return response.json()
    else:
        logger.error(f"Failed to get market status: {response.text}")
        return False


def get_stock_id_by_symbol(symbol):
    global stock_ids
    if symbol in stock_ids:
        return stock_ids[symbol]
    raise ValueError("Stock symbol not found.")


@cached(cache)
def get_stock_price_from_dinari(symbol, logger):
    stock_id = get_stock_id_by_symbol(symbol)
    url = f"https://api.sbt.dinari.com/api/v1/stocks/price_summaries?stock_ids={stock_id}"
    response = requests.get(url)
    logger.debug(f"Fetching stock price for {symbol} {response.status_code}:{response.json()}")
    try:
        if response.status_code == 200:
            data = response.json()
            if data and len(data) > 0:
                stock_info = data[0]
                bp_price = f'{stock_info.get("price") :.2f}'
                ap_price = f'{stock_info.get("price") :.2f}'
                return bp_price, ap_price
            else:
                raise ValueError("No data found for the given stock ID.")
        else:
            response.raise_for_status()
    except Exception as e:
        logger.error(e)
        return 0, 0
