import json
import calendar
import time

import requests
from cachetools import TTLCache, cached

from constants.constant import REQUEST_TIMEOUT, CONFIG, PositionStatus
from util.crypto import XCH_MOJO, get_crypto_balance, get_crypto_price, sign_message_by_key

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
        input_data = {"pool_id": CONFIG["POOL_ID"], "status": 2, "start_index": 0, "num_of_transactions": 200}
        response = requests.post(url, json=input_data, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            orders = response.json()
            amount = 0
            for o in orders:
                if o["type"] % 2 == 0:
                    amount += int(o["request"]) / XCH_MOJO
            return amount
        else:
            raise Exception(f"Failed to get pending ordersfor {CONFIG['POOL_ID']} {response.status_code} : {response.text}")
    except Exception as e:
        raise e


def get_fund_value(logger):
    try:
        pool = get_pool_by_id(CONFIG["POOL_ID"])
        data = json.loads(pool["description"])
        usd_value = 0
        for asset in data["assets"]:
            usd_value += asset["value"]
        return usd_value
    except Exception as e:
        logger.error(f"Failed to get fund value {CONFIG['POOL_ID']}. {e}")
        return 0

def check_cash_reserve(traders, fund_xch, is_buy, logger):
    try:
        required_amount = get_pending_sell_orders(logger)
        xch_balance = get_crypto_balance()
        pending_sell_amount = 0
        xch_price = get_crypto_price(logger)
        for t in traders:
            if t.position_status == PositionStatus.PENDING_LIQUIDATION.name:
                pending_sell_amount += t.volume * t.current_price / xch_price
        logger.info(f"Required amount:{required_amount}, Current amount: {xch_balance + pending_sell_amount}")
        if required_amount > xch_balance + pending_sell_amount or ((fund_xch -required_amount) * CONFIG["RESERVE_RATIO"] > xch_balance - required_amount and is_buy):
            return False
        else:
            return True
    except Exception as e:
        logger.error(f"Failed to check reverse fund {CONFIG['FUND_ID']}. {e}")
        raise e


def get_user_transactions(did_id, status=1, start_index=0, num_of_transactions=100, sort_by_ascending=False, logger=None):
    """
    Get user transactions from SharesDAO API
    
    Args:
        did_id: DID ID in hex format
        status: Transaction status (default: 1)
        start_index: Starting index for pagination (default: 0)
        num_of_transactions: Number of transactions to retrieve (default: 100)
        sort_by_ascending: Sort order, False for descending (default: False)
        logger: Optional logger instance
        
    Returns:
        List of transaction dictionaries
        
    Raises:
        Exception: If API request fails
    """
    url = f"https://api.sharesdao.com:8443/transaction/user"
    
    try:
        # Generate timestamp
        timestamp = str(calendar.timegm(time.gmtime()))
        
        # Create signature message: SharesDAO|Login|{timestamp}
        message = f"SharesDAO|Login|{timestamp}"
        signature = sign_message_by_key(message)
        
        # Prepare request payload
        payload = {
            "did_id": did_id,
            "timestamp": timestamp,
            "status": status,
            "start_index": start_index,
            "num_of_transactions": num_of_transactions,
            "sort_by_ascending": sort_by_ascending,
            "signature": signature
        }
        
        if logger:
            logger.debug(f"Requesting user transactions: {payload}")
        
        # Send POST request
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        
        if response.status_code == 200:
            transactions = response.json()
            if logger:
                logger.info(f"Retrieved {len(transactions)} transactions for DID {did_id}")
            return transactions
        else:
            error_msg = f"Failed to get user transactions for DID {did_id}. Status: {response.status_code}, Response: {response.text}"
            if logger:
                logger.error(error_msg)
            raise Exception(error_msg)
            
    except requests.exceptions.RequestException as e:
        error_msg = f"API request failed for user transactions: {e}"
        if logger:
            logger.error(error_msg)
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"Failed to get user transactions: {e}"
        if logger:
            logger.error(error_msg)
        raise Exception(error_msg)
