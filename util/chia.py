import calendar
import datetime
import json
import os
import re
import subprocess
import time
from datetime import datetime
import requests
from cachetools import TTLCache, cached
from chia.types.blockchain_format.program import Program
from chia.types.signing_mode import CHIP_0002_SIGN_MESSAGE_PREFIX
from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import calculate_synthetic_secret_key, \
    DEFAULT_HIDDEN_PUZZLE_HASH
from chia_rs import PrivateKey, AugSchemeMPL

from util.bech32m import encode_puzzle_hash
from constants.constant import PositionStatus, CONFIG, REQUEST_TIMEOUT, StrategyType
from util.db import update_position, get_last_trade, delete_trade
from util.stock import STOCKS

coin_cache = TTLCache(maxsize=100, ttl=600)
price_cache = TTLCache(maxsize=100, ttl=30)
tx_cache = TTLCache(maxsize=100, ttl=30)
balance_cache = TTLCache(maxsize=10, ttl=10)
token_cache = TTLCache(maxsize=10, ttl=10)
last_checked_tx = {}
CHIA_PATH = "chia"
XCH_MOJO = 1000000000000
CAT_MOJO = 1000


def send_asset(address: str, wallet_id: int, request: float, offer: float, logger, cid="", order_type="LIMIT"):
    if wallet_id == 1:
        offer_amount = int(offer * XCH_MOJO)
        request_amount = int(request * CAT_MOJO)
        amount = offer_amount / XCH_MOJO
    else:
        offer_amount = int(offer * CAT_MOJO)
        request_amount = int(request * XCH_MOJO)
        amount = offer_amount / CAT_MOJO
    try:
        result = subprocess.check_output(
            [CHIA_PATH, "wallet", "send", f'--fingerprint={CONFIG["WALLET_FINGERPRINT"]}', f'--id={wallet_id}',
             f"--address={address}", f"--amount={amount}", f'--fee={CONFIG["CHIA_TX_FEE"]}', "--reuse", "-e",
             '{"did_id":"' + CONFIG["DID_HEX"] + '","customer_id":"' + cid + '", "type":"' + order_type.upper() + '" "offer":' + str(offer_amount) + ', "request":' + str(
                 request_amount) + '}']).decode(
            "utf-8")
        if result.find("SUCCESS") > 0:
            logger.info(f"Sent {offer_amount} wallet_id {wallet_id} to {address}")
            return True
        else:
            if result.find("Can't spend more than wallet balance") > 0:
                logger.error(f"Insufficient balance to send {offer_amount} wallet_id {wallet_id} to {address}")
                return False
            logger.error(f"Failed to sent {offer_amount} wallet_id {wallet_id} to {address}: {result}")
            return False
    except Exception as e:
        logger.error(
            f"Failed to sent {offer_amount} wallet_id {wallet_id} to {address}, please check your Chia wallet: {e}")
        return False


def trade(ticker, side, request, offer,logger, customer_id, order_type="LIMIT"):
    now = calendar.timegm(time.gmtime())
    inputs = {
        "timestamp": now,
        "signature": "signature",
        "order": {
            "symbol": ticker,
            "side": side,
            "request": request,
            "offer": offer,
            "type": order_type,
            "customer_id": customer_id,
        },
    }
    try:
        inputs["signature"] = sign_message_by_key(f"{json.dumps(inputs['order'])}|{now}")
        url = f"{CONFIG['VAULT_HOST']}:8888/trade"
        response = requests.post(url, data=json.dumps(inputs), timeout=REQUEST_TIMEOUT)
        if response.status_code == 200 and response.json()["status"] == "Success":
            logger.info(f"Traded {ticker} {side} {request} {offer}")
            return True
        else:
            logger.error(f"Failed to trade {ticker} {side} {request} {offer}: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Failed to trade {ticker} {side} {request} {offer}: {e}")
        return False


def get_xch_txs():
    url = f"https://api.spacescan.io/address/xch-transaction/{CONFIG['ADDRESS']}"

    # Request with parameters
    params = {
        "include_send_dust": "false",
        "include_received_dust": "false",
        "include_send": "false",
        "include_received": "true",
        "count": 100
    }

    response = requests.get(url, params=params)
    data = response.json()
    if data["status"] != "success":
        raise Exception("Failed to get XCH transactions")
    for tx in data["received_transactions"]["transactions"]:
        tx["sent"] = 0
        tx["amount"] = tx["amount_mojo"]
        try:
            if len(tx["memo"][0]) > 81:
                decoded_string = bytes.fromhex(tx["memo"][0]).decode('utf-8')
            else:
                decoded_string = bytes.fromhex(tx["memo"][1]).decode('utf-8')
            response = json.loads(decoded_string)
            tx["memo"] = response
        except Exception as e:
            tx["memo"] = {"customer_id": "", "symbol": ""}
    return data["received_transactions"]["transactions"]


def get_cat_txs():
    url = f"https://api.spacescan.io/address/token-transaction/{CONFIG['ADDRESS']}"

    # Request with parameters
    params = {
        "send_cursor": "100",
        "count": 200
    }
    cat_txs = {}
    response = requests.get(url, params=params)
    data = response.json()
    if data["status"] != "success":
        raise Exception("Failed to get XCH transactions")
    for tx in data["received_transactions"]["transactions"]:
        tx["sent"] = 0
        tx["amount"] = tx["token_amount"] * CAT_MOJO
        try:
            if len(tx["memo"][0]) > 81:
                decoded_string = bytes.fromhex(tx["memo"][0]).decode('utf-8')
            else:
                decoded_string = bytes.fromhex(tx["memo"][1]).decode('utf-8')
            response = json.loads(decoded_string)
            tx["memo"] = response
        except Exception as e:
            tx["memo"] = {"customer_id": "", "symbol": ""}
        if tx["asset_id"].lower() not in cat_txs:
            cat_txs[tx["asset_id"].lower()] = []
        cat_txs[tx["asset_id"].lower()].append(tx)
    return cat_txs


def check_pending_positions(traders, logger):
    xch_txs = get_xch_txs()
    all_cat_txs = get_cat_txs()
    token_balance = get_token_balance()
    logger.info(f"Fetched {len(xch_txs)} XCH txs.")
    for trader in traders:
        confirmed = False
        logger.info(f"Checking {trader.stock}, status: {trader.position_status}")
        if trader.position_status == PositionStatus.PENDING_BUY.name:
            if trader.type == StrategyType.DCA:
                # Check if the pending buy is confirmed
                expect_amount = trader.volume
                amount = token_balance[STOCKS[trader.ticker]["token_id"]]["balance"]
                if amount - expect_amount >= -0.003:
                    trader.position_status = PositionStatus.TRADABLE.name
                    trader.volume = amount
                    update_position(trader)
                    logger.info(f"Buy {trader.stock} confirmed")
                    confirmed = True
            if trader.type == StrategyType.GRID:
                if STOCKS[trader.ticker]["asset_id"].lower() not in all_cat_txs:
                    all_cat_txs[STOCKS[trader.ticker]["asset_id"].lower()] = []
                cat_txs = all_cat_txs[STOCKS[trader.ticker]["asset_id"].lower()]
                for tx in cat_txs:
                    if tx["sent"] == 0:
                        try:
                            if "customer_id" in tx["memo"] and tx["memo"]["customer_id"] == trader.stock:
                                if "order_id" in tx["memo"] and tx["memo"]["order_id"] > str(
                                        trader.last_updated.timestamp() - CONFIG["MAX_ORDER_TIME_OFFSET"]):
                                    if tx["memo"]["status"] == "COMPLETED":
                                        trader.position_status = PositionStatus.TRADABLE.name
                                        trader.volume = tx["amount"] / CAT_MOJO
                                        update_position(trader)
                                        logger.info(f"Buy {trader.stock} confirmed")
                                        confirmed = True
                                        break
                        except Exception as e:
                            continue
            # Check if the order is cancelled
            if confirmed:
                continue
            for tx in xch_txs:
                if tx["sent"] == 0:
                    try:
                        # Check if the order is cancelled
                        logger.debug(f"Checking buy cancellation:{tx['memo']}")
                        if "symbol" in tx["memo"] and tx["memo"]["symbol"] == trader.ticker:
                            if "order_id" in tx["memo"] and tx["memo"]["order_id"] > str(
                                    trader.last_updated.timestamp() - CONFIG["MAX_ORDER_TIME_OFFSET"]):
                                if tx["memo"]["status"] == "CANCELLED":
                                    if trader.type == StrategyType.DCA or (
                                            trader.type == StrategyType.GRID and trader.stock == tx["memo"]["customer_id"]):
                                        last_trade = get_last_trade(trader.stock)
                                        trader.volume -= last_trade[4]
                                        trader.total_cost -= last_trade[5]
                                        trader.position_status = PositionStatus.TRADABLE.name
                                        if trader.type == StrategyType.DCA:
                                            trader.buy_count -= 1
                                        trader.last_updated = datetime.now()
                                        update_position(trader)
                                        delete_trade(last_trade[0])
                                        last_trade = get_last_trade(trader.stock)
                                        if last_trade is None or last_trade[2] == 'SELL':
                                            trader.last_buy_price = 0
                                            trader.avg_price = 0
                                            trader.volume = 0
                                            trader.total_cost = 0
                                        else:
                                            trader.avg_price = trader.total_cost / trader.volume
                                            trader.last_buy_price = last_trade[3]
                                        update_position(trader)
                                        confirmed = True
                                        logger.info(f"Buy {trader.stock} cancelled")
                                        break
                    except Exception as e:
                        continue
        if trader.position_status == PositionStatus.PENDING_SELL.name:
            # Check if the order is cancelled
            if STOCKS[trader.ticker]["asset_id"].lower() not in all_cat_txs:
                all_cat_txs[STOCKS[trader.ticker]["asset_id"].lower()] = []
            cat_txs = all_cat_txs[STOCKS[trader.ticker]["asset_id"].lower()]
            for tx in cat_txs:
                if tx["sent"] == 0:
                    try:
                        if "symbol" in tx["memo"] and tx["memo"]["symbol"] == trader.ticker:
                            if "order_id" in tx["memo"] and tx["memo"]["order_id"] > str(
                                    trader.last_updated.timestamp() - CONFIG["MAX_ORDER_TIME_OFFSET"]):
                                if tx["memo"]["status"] == "CANCELLED":
                                    if trader.type == StrategyType.DCA or trader.stock == tx["memo"]["customer_id"]:
                                        trader.position_status = PositionStatus.TRADABLE.name
                                        trader.last_updated = datetime.now()
                                        update_position(trader)
                                        last_trade = get_last_trade(trader.stock)
                                        delete_trade(last_trade[0])
                                        confirmed = True
                                        logger.info(f"Sell {trader.stock} cancelled")
                                        break
                    except Exception as e:
                        continue
            if confirmed:
                continue
            # Check if the order is completed
            for tx in xch_txs:
                if tx["sent"] == 0:

                    try:
                        if "symbol" in tx["memo"] and tx["memo"]["symbol"] == trader.ticker:
                            logger.debug(
                                f"Last Update {str(trader.last_updated.timestamp())}, Order: {tx['memo']['order_id']}")
                            if "order_id" in tx["memo"] and tx["memo"]["order_id"] > str(
                                    trader.last_updated.timestamp() - CONFIG["MAX_ORDER_TIME_OFFSET"]):
                                if tx["memo"]["status"] == "COMPLETED":
                                    if trader.type == StrategyType.DCA:
                                        # The order is created after the last update
                                        trader.profit = 0
                                        trader.position_status = PositionStatus.TRADABLE.name
                                        trader.volume = 0
                                        trader.buy_count = 0
                                        trader.last_buy_price = 0
                                        trader.total_cost = 0
                                        trader.avg_price = 0
                                        trader.current_price = 0

                                        trader.last_updated = datetime.now()
                                        update_position(trader)
                                        logger.info(f"Sell {trader.stock} confirmed")
                                        break
                                    if trader.type == StrategyType.GRID and trader.stock == tx["memo"]["customer_id"]:
                                        # The order is created after the last update
                                        trader.profit += tx["amount"]/XCH_MOJO - trader.total_cost
                                        trader.position_status = PositionStatus.TRADABLE.name
                                        trader.volume = 0
                                        trader.buy_count = trader.buy_count+1
                                        trader.last_buy_price = 0
                                        trader.total_cost = 0
                                        trader.avg_price = 0
                                        trader.current_price = 0
                                        trader.last_updated = datetime.now()
                                        update_position(trader)
                                        logger.info(f"Sell {trader.stock} confirmed")
                                        break
                    except Exception as e:
                        logger.error(f"Failed to confirm {trader.stock}: {e}")
                        continue
    return False


@cached(balance_cache)
def get_xch_balance():
    url = f"https://api.spacescan.io/address/xch-balance/{CONFIG['ADDRESS']}"
    try:
        response = requests.get(url)
        data = response.json()
        if data["status"] == "success":
            return data["xch"]
        else:
            return 0
    except Exception as e:
        print(f"Cannot get XCH balance")
        return None


@cached(token_cache)
def get_token_balance():
    url = f"https://api.spacescan.io/address/token-balance/{CONFIG['ADDRESS']}"
    response = requests.get(url)
    data = response.json()
    if data["status"] == "success":
        return {t["asset_id"]: t for t in data["data"]}
    else:
        return {}


def add_token(symbol):
    pass


@cached(price_cache)
def get_xch_price(logger):
    url = f"https://api.sharesdao.com:8443/util/get_price/XCH"
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)

        if response.status_code == 200:
            return response.json()["XCH"]
        else:
            logger.error(f"Error: {response.status_code}")
            return None
    except Exception as e:
        print(f"Cannot get XCH price")
        return None


@cached(coin_cache)
def get_coin_info(coin_id, logger):
    url = f"https://api-fin.spacescan.io/coin/info/{coin_id}?version=0.1.0&network=mainnet"
    response = requests.get(url,  timeout=REQUEST_TIMEOUT)

    if response.status_code == 200:
        return response.json()
    else:
        logger.error(f"Error: {response.status_code}")
        return None



def sign_message_by_wallet(did, message):
    try:
        did_id = encode_puzzle_hash(did, "did:chia:")
        response = subprocess.check_output(
            [CHIA_PATH, "rpc", "wallet", "sign_message_by_id", '{"id":"' + did_id + '", "message":"' + message + '"}'],
            stderr=subprocess.DEVNULL).decode("utf-8")
        signature = json.loads(response)
        return signature["signature"]
    except Exception as e:
        print(f"Cannot sign message {message} with DID {did}")
        raise e

def sign_message_by_key(message):
    private_key = PrivateKey.from_bytes(bytes.fromhex(os.environ["DID_PRIVATE_KEY"]))
    synthetic_secret_key = calculate_synthetic_secret_key(private_key, DEFAULT_HIDDEN_PUZZLE_HASH)
    hex_message = Program.to((CHIP_0002_SIGN_MESSAGE_PREFIX, message)).get_tree_hash()
    return str(AugSchemeMPL.sign(synthetic_secret_key, hex_message))