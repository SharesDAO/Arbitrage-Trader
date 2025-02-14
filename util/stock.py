import requests
from cachetools import TTLCache, cached

from constants.constant import REQUEST_TIMEOUT, CONFIG, PositionStatus
from util.chia import get_xch_balance, XCH_MOJO

cache = TTLCache(maxsize=100, ttl=20)
clock = TTLCache(maxsize=1, ttl=60)

def get_pool_list():
    url = "https://api.sharesdao.com:8443/pool/list"
    try:
        response = requests.post(url, timeout=REQUEST_TIMEOUT, json={"type": 2})

        if response.status_code == 200:
            stocks = response.json()
            pools = {}
            for s in stocks:
                pools[s["symbol"]] = {"asset_id": s["token_id"], "buy_addr": s["mint_address"], "sell_addr": s["burn_address"], "pool_id": s["pool_id"]}
            return pools
        else:
            raise Exception(f"Failed to get stock pools list {response.status_code}")
    except Exception as e:
        raise e

STOCKS = get_pool_list()

@cached(clock)
def is_market_open(logger) -> bool:
    url = "https://api.sharesdao.com:8443/util/market_status"
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)

        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"Failed to get market status: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Failed to get market status: {e}")
        return False


def get_stock_id_by_symbol(symbol):
    global stock_ids
    if symbol in stock_ids:
        return stock_ids[symbol]
    raise ValueError("Stock symbol not found.")


@cached(cache)
def get_stock_price(symbol, logger):
    return get_stock_price_from_dao(symbol, logger)


def get_stock_price_from_dao(symbol, logger):
    url = f"https://api.sharesdao.com:8443/pool/{STOCKS[symbol]['pool_id']}"
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)

        if response.status_code == 200:
            pool = response.json()
            bp_price = f'{float(pool["buy_price"]) * (1 - CONFIG["SLIPPAGE"]):.2f}'
            ap_price = f'{float(pool["sell_price"]) * (1 + CONFIG["SLIPPAGE"]) :.2f}'
            return float(bp_price), float(ap_price)
        else:
            logger.error(f"Failed to get stock price for {symbol} {response.status_code} : {response.text}")
            return 0, 0
    except Exception as e:
        logger.error(f"Failed to get stock price for {symbol}. {e}")
        return 0, 0


