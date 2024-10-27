import requests
from cachetools import TTLCache, cached

cache = TTLCache(maxsize=100, ttl=1)
clock = TTLCache(maxsize=1, ttl=1)


@cached(clock)
def is_market_open() -> bool:
    url = "https://www.sharesdao.com:8443/util/market_status"

    response = requests.get(url)

    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Failed to get market status: {response.text}")


def fetch_token_infos():
    url = "https://api.sbt.dinari.com/api/v1/chain/42161/token_infos"
    response = requests.get(url)

    if response.status_code == 200:
        return response.json()
    else:
        response.raise_for_status()


def get_stock_id_by_symbol(symbol):
    if symbol in cache:
        return cache[symbol]

    token_infos = fetch_token_infos()
    for token_info in token_infos:
        stock = token_info.get('stock', {})
        if stock.get('symbol') == symbol:
            stock_id = stock.get('id')
            # 将symbol和stock_id存入缓存
            cache[symbol] = stock_id
            return stock_id

    raise ValueError("Stock symbol not found.")


@cached(cache)
def get_stock_price_from_dinari(symbol):
    stock_id = get_stock_id_by_symbol(symbol)
    url = f"https://api.sbt.dinari.com/api/v1/stocks/price_summaries?stock_ids={stock_id}"
    response = requests.get(url)

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
