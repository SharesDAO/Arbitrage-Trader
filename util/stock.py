import json
import requests
from cachetools import TTLCache, cached

from constants.constant import REQUEST_TIMEOUT, CONFIG

cache = TTLCache(maxsize=100, ttl=20)
clock = TTLCache(maxsize=1, ttl=60)

def get_pool_list(blockchain):
    url = "https://api.sharesdao.com:8443/pool/list"
    try:
        response = requests.post(url, timeout=REQUEST_TIMEOUT, json={"type": 2})

        if response.status_code == 200:
            stocks = response.json()
            pools = {}
            for s in stocks:
                # For EVM, filter by blockchain type in response (blockchain field is 6, not a string)
                if blockchain == 6:
                    # EVM pools have blockchain field as number 6
                    if s.get("blockchain") == 6:
                        # Parse token_id which is a JSON object with chain-specific addresses
                        token_id = s["token_id"]
                        asset_id = token_id
                        if isinstance(token_id, str):
                            try:
                                token_id_dict = json.loads(token_id)
                                # Get the address for current EVM chain
                                evm_chain = CONFIG.get("EVM_CHAIN", "").lower()
                                # Map chain names: bsc -> bnb (API uses "bnb" but we use "bsc" in config)
                                # Try bnb first (since API uses "bnb"), then bsc
                                if evm_chain == "bsc":
                                    if "bnb" in token_id_dict:
                                        asset_id = token_id_dict["bnb"]
                                    elif "bsc" in token_id_dict:
                                        asset_id = token_id_dict["bsc"]
                                    elif evm_chain in token_id_dict:
                                        asset_id = token_id_dict[evm_chain]
                                    else:
                                        # Try to find any address in the dict
                                        asset_id = list(token_id_dict.values())[0] if token_id_dict else token_id
                                else:
                                    # For other chains, try exact match first
                                    if evm_chain in token_id_dict:
                                        asset_id = token_id_dict[evm_chain]
                                    else:
                                        # Try alternative names
                                        alt_names = {
                                            "ethereum": ["eth", "ethereum"],
                                            "arbitrum": ["arbitrum", "arb"],
                                            "base": ["base"]
                                        }
                                        for alt in alt_names.get(evm_chain, []):
                                            if alt in token_id_dict:
                                                asset_id = token_id_dict[alt]
                                                break
                                        else:
                                            # If still not found, try to get any address
                                            if token_id_dict:
                                                asset_id = list(token_id_dict.values())[0]
                            except (json.JSONDecodeError, TypeError):
                                # If it's not JSON, use as is
                                asset_id = token_id
                        
                        pools[s["symbol"]] = {
                            "blockchain": s["blockchain"], 
                            "asset_id": asset_id, 
                            "buy_addr": s["mint_address"], 
                            "sell_addr": s["burn_address"], 
                            "pool_id": s["pool_id"]
                        }
                else:
                    if s["blockchain"] == blockchain: 
                        pools[s["symbol"]] = {"blockchain":s["blockchain"], "asset_id": s["token_id"], "buy_addr": s["mint_address"], "sell_addr": s["burn_address"], "pool_id": s["pool_id"]}
            return pools
        else:
            raise Exception(f"Failed to get stock pools list {response.status_code}")
    except Exception as e:
        raise e

STOCKS = {}

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
        logger.error(f"Failed to get stock price for {symbol}, {str(e)}")
        return 0, 0