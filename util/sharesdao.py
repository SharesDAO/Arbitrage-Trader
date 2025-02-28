import json

import requests
from cachetools import TTLCache, cached

from constants.constant import REQUEST_TIMEOUT, CONFIG, PositionStatus
from util.chia import XCH_MOJO, get_xch_balance, get_xch_price

order_cache = TTLCache(maxsize=100, ttl=40)

def get_pool_by_id(pool_id):
    url = f"https://api.sharesdao.com:8443/pool/{pool_id}"
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)

        if response.status_code == 200:
            pool = response.json()
            return pool
        else:
            raise Exception(f"Failed to get pool {pool_id} {response.status_code} : {response.text}")
    except Exception as e:
        raise e

@cached(order_cache)
def get_pending_sell_orders(logger):
    url = f"https://api.sharesdao.com:8443/transaction/pool"
    try:
        input_data = {"pool_id": CONFIG["FUND_ID"], "status": 2, "start_index": 0, "num_of_transactions": 200}
        response = requests.post(url, json=input_data, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            orders = response.json()
            amount = 0
            for o in orders:
                if o["type"] % 2 == 0:
                    amount += int(o["request"]) / XCH_MOJO
            return amount
        else:
            raise Exception(f"Failed to get pending ordersfor {CONFIG['FUND_ID']} {response.status_code} : {response.text}")
    except Exception as e:
        raise e


def get_fund_value(logger):
    try:
        pool = get_pool_by_id(CONFIG["FUND_ID"])
        data = json.loads(pool["description"])
        usd_value = 0
        for asset in data["assets"]:
            usd_value += asset["value"]
        return usd_value
    except Exception as e:
        logger.error(f"Failed to get fund value {CONFIG['FUND_ID']}. {e}")
        return 0

def check_cash_reserve(traders, logger):
    try:
        required_amount = get_pending_sell_orders(logger)
        xch_balance = get_xch_balance()
        pending_sell_amount = 0
        xch_price = get_xch_price(logger)
        for t in traders:
            if t.position_status == PositionStatus.PENDING_LIQUIDATION.name:
                pending_sell_amount += t.volume * t.current_price / xch_price
        logger.info(f"Required amount:{required_amount}, Current amount: {xch_balance + pending_sell_amount}")
        if required_amount > xch_balance + pending_sell_amount:
            return False
        else:
            return True
    except Exception as e:
        logger.error(f"Failed to check reverse fund {CONFIG['FUND_ID']}. {e}")
        raise e
