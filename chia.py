import datetime
import json
import re
import subprocess
from datetime import datetime
import requests
from cachetools import TTLCache, cached
from constant import CHIA_PATH, CHIA_TX_FEE, DID_HEX, PositionStatus, STOCKS, WALLET_FINGERPRINT, MAX_ORDER_TIME_OFFSET
from db import update_position, get_last_trade, delete_trade

coin_cache = TTLCache(maxsize=100, ttl=600)
last_checked_tx = {}

def send_asset(address: str, wallet_id: int, request: float, offer: float, logger):
    if wallet_id == 1:
        offer_amount = int(offer * 1000000000000)
        request_amount = int(request * 1000)
        amount = offer_amount / 1000000000000
    else:
        offer_amount = int(offer * 1000)
        request_amount = int(request * 1000000000000)
        amount = offer_amount / 1000
    result = subprocess.check_output(
        [CHIA_PATH, "wallet", "send", f"--fingerprint={WALLET_FINGERPRINT}", f"--id={wallet_id}",
         f"--address={address}", f"--amount={amount}", f"--fee={CHIA_TX_FEE}", "--reuse", "-e",
         '{"did_id":"' + DID_HEX + '", "offer":' + str(offer_amount) + ', "request":' + str(
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

def get_chia_txs(wallet_id=1, num=50):
    global last_checked_tx
    request = '{"wallet_id":'+str(wallet_id)+', "reverse": true, "end":'+str(num)+'}'
    result = subprocess.check_output([CHIA_PATH, "rpc", "wallet", "get_transactions", request]).decode("utf-8")
    txs = json.loads(result)["transactions"]
    if wallet_id in last_checked_tx:
        filtered_txs = []
        for tx in txs:
            if tx["name"] != last_checked_tx[wallet_id]:
                filtered_txs.append(tx)
            else:
                break
        txs = filtered_txs
    if len(txs) > 0:
        last_checked_tx[wallet_id] = txs[0]["name"]
    return txs

def check_pending_positions(traders, logger):
    xch_txs = get_chia_txs()
    balance_result = subprocess.check_output([CHIA_PATH, "wallet", "show", f"--fingerprint={WALLET_FINGERPRINT}"]).decode(
        "utf-8").split("\n")
    logger.debug(f"Found {len(balance_result)} wallets")
    for trader in traders:
        confirmed = False
        logger.info(f"Checking {trader.stock} pending trades ...")
        if trader.position_status == PositionStatus.PENDING_BUY.name:
            # Check if the pending buy is confirmed
            expect_amount = trader.volume
            wallet_name = trader.stock
            for l in range(len(balance_result)):
                if balance_result[l].find(wallet_name) >= 0:
                    amount = float(re.search(r"^   -Spendable:             ([\.0-9]+?) .*$", balance_result[l + 3]).group(1))
                    if amount - expect_amount >= -0.003:
                        trader.position_status = PositionStatus.TRADABLE.name
                        trader.volume = amount
                        update_position(trader)
                        logger.info(f"Buy {trader.stock} confirmed")
                        confirmed = True
                        break
            # Check if the order is cancelled
            if confirmed:
                continue
            for tx in xch_txs:
                if tx["sent"] == 0:
                    request = '{"transaction_id": "' + tx["name"] + '"}'
                    try:
                        # Check if the order is cancelled
                        t = datetime.now()
                        logger.debug(
                            f"Get coin info for {trader.stock}, spent {datetime.now().timestamp() - t.timestamp()}")
                        memo = subprocess.check_output([CHIA_PATH, "rpc", "wallet", "get_transaction_memo", request],
                                                       stderr=subprocess.DEVNULL).decode(
                            "utf-8")
                        memo = json.loads(memo)
                        decoded_string = bytes.fromhex(memo[tx["name"][2:]][tx["name"][2:]][1]).decode('utf-8')
                        logger.debug(f"Found coin with memo for {trader.stock}, memo: {decoded_string}")
                        response = json.loads(decoded_string)
                        if "symbol" in response and response["symbol"] == trader.stock:
                            if "order_id" in response and response["order_id"] > str(trader.last_updated.timestamp() - MAX_ORDER_TIME_OFFSET):
                                if response["status"] == "CANCELLED":
                                    last_trade = get_last_trade(trader.stock)
                                    trader.volume -= last_trade[4]
                                    trader.total_cost -= last_trade[5]
                                    trader.position_status = PositionStatus.TRADABLE.name
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

            cat_txs = get_chia_txs(trader.wallet_id, 10)
            for tx in cat_txs:
                if tx["sent"] == 0:
                    request = '{"transaction_id": "' + tx["name"] + '"}'
                    try:
                        # Check if the order is cancelled
                        t = datetime.now()
                        logger.debug(
                            f"Get coin info for {trader.stock}, spent {datetime.now().timestamp() - t.timestamp()}")
                        memo = subprocess.check_output([CHIA_PATH, "rpc", "wallet", "get_transaction_memo", request],
                                                       stderr=subprocess.DEVNULL).decode(
                            "utf-8")
                        memo = json.loads(memo)
                        decoded_string = bytes.fromhex(memo[tx["name"][2:]][tx["name"][2:]][1]).decode('utf-8')
                        logger.debug(f"Found coin with memo for {trader.stock}, memo: {decoded_string}")
                        response = json.loads(decoded_string)
                        if "symbol" in response and response["symbol"] == trader.stock:
                            if "order_id" in response and response["order_id"] > str(trader.last_updated.timestamp() - MAX_ORDER_TIME_OFFSET):
                                if response["status"] == "CANCELLED":
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
                    request = '{"transaction_id": "'+tx["name"]+'"}'
                    try:
                        t = datetime.now()
                        logger.debug(f"Get coin info for {trader.stock}, spent {datetime.now().timestamp()-t.timestamp()}")
                        memo = subprocess.check_output([CHIA_PATH, "rpc", "wallet", "get_transaction_memo", request],stderr=subprocess.DEVNULL).decode(
                        "utf-8")
                        memo = json.loads(memo)
                        decoded_string = bytes.fromhex(memo[tx["name"][2:]][tx["name"][2:]][0]).decode('utf-8')
                        response = json.loads(decoded_string)
                        logger.debug(f"Found coin with memo for {trader.stock}, memo: {decoded_string}")
                        if "symbol" in response and response["symbol"] == trader.stock:
                            logger.debug(f"Last Update {str(trader.last_updated.timestamp())}, Order: {response['order_id']}")
                            if "order_id" in response and response["order_id"] > str(trader.last_updated.timestamp() - MAX_ORDER_TIME_OFFSET):
                                if response["status"] == "COMPLETED":
                                    if trader.position_status == PositionStatus.PENDING_SELL.name:
                                        # The order is created after the last update
                                        trader.position_status = PositionStatus.TRADABLE.name
                                        trader.volume = 0
                                        trader.buy_count = 0
                                        trader.last_buy_price = 0
                                        trader.total_cost = 0
                                        trader.avg_price = 0
                                        trader.current_price = 0
                                        trader.profit = 0
                                        trader.last_updated = datetime.now()
                                        update_position(trader)
                                        logger.info(f"Sell {trader.stock} confirmed")
                                        break
                    except Exception as e:
                        continue

    return False


def get_xch_balance():
    wallet_name = "Chia Wallet"
    result = subprocess.check_output([CHIA_PATH, "wallet", "show", f"--fingerprint={WALLET_FINGERPRINT}"]).decode(
        "utf-8").split("\n")
    for l in range(len(result)):
        if result[l].find(wallet_name) >= 0:
            amount = float(re.search(r"^   -Spendable:             ([\.0-9]+?) .*$", result[l + 3]).group(1))
            return amount
    return 0


def add_token(symbol):
    result = subprocess.check_output([CHIA_PATH, "wallet", "add_token", f"--fingerprint={WALLET_FINGERPRINT}",
                                      f"--asset-id={STOCKS[symbol]['asset_id']}", f"--token-name={symbol}"]).decode(
        "utf-8")
    if result.find("Successfully added") >= 0:
        return int(re.search(r"^Successfully added.*wallet id ([\.\d]+?) .*$", result).group(1))
    elif result.find("Successfully renamed") >= 0:
        return int(re.search(r"^Successfully renamed.*wallet_id ([\.\d]+?) .*$", result).group(1))


def get_xch_price(logger):
    url = f"https://api.sharesdao.com:8443/util/get_price/XCH"
    response = requests.get(url)

    if response.status_code == 200:
        return response.json()["XCH"]
    else:
        logger.error(f"Error: {response.status_code}")
        return None

@cached(coin_cache)
def get_coin_info(coin_id, logger):
    url = f"https://api-fin.spacescan.io/coin/info/{coin_id}?version=0.1.0&network=mainnet"
    response = requests.get(url)

    if response.status_code == 200:
        return response.json()
    else:
        logger.error(f"Error: {response.status_code}")
        return None