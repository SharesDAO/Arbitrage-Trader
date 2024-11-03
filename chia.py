import json
import re
import subprocess

import requests

from constant import CHIA_PATH, CHIA_TX_FEE, DID_HEX, PositionStatus, STOCKS, WALLET_FINGERPRINT
last_checked_tx = None
tx_cache = TTLCache(maxsize=1, ttl=10)

def send_asset(address: str, wallet_id: int, request: float, offer: float, logger):
    if wallet_id == 1:
        offer_amount = int(offer * 1000000000000)
        request_amount = int(request * 1000)
    else:
        offer_amount = int(offer * 1000)
        request_amount = int(request * 1000000000000)
    result = subprocess.check_output(
        [CHIA_PATH, "wallet", "send", f"--fingerprint={WALLET_FINGERPRINT}", f"--id={wallet_id}",
         f"--address={address}", f"--amount={offer}", f"--fee={CHIA_TX_FEE}", "--reuse", "-e",
         '{"did_id":"' + DID_HEX + '", "offer":' + str(offer_amount) + ', "request":' + str(
             request_amount) + '}']).decode(
        "utf-8")
    if result.find("SUCCESS") > 0:
        logger.info(f"Sent {offer_amount} wallet_id {wallet_id} to {address}")
        return True
    else:
        logger.error(f"Failed to sent {offer_amount} wallet_id {wallet_id} to {address}: {result}")
        return False

def get_chia_txs():
    request = '{"wallet_id":1, "reverse": true}'
    result = subprocess.check_output([CHIA_PATH, "rpc", "wallet", "get_transactions", request]).decode("utf-8")
    txs = json.loads(result)["transactions"]
    if last_checked_tx is not None:
        filtered_txs = []
        for tx in txs:
            if tx["name"] != last_checked_tx:
                filtered_txs.append(tx)
            else:
                break
        txs = filtered_txs
    if len(txs) > 0:
        last_checked_tx = txs[0]["name"]
    return txs

def check_pending_positions(traders):
    if stock.position_status == PositionStatus.PENDING_BUY.name:
        expect_amount = stock.volume
        wallet_name = stock.stock
        result = subprocess.check_output([CHIA_PATH, "wallet", "show", f"--fingerprint={WALLET_FINGERPRINT}"]).decode(
            "utf-8").split("\n")
        for l in range(len(result)):
            if result[l].find(wallet_name) >= 0:
                amount = float(re.search(r"^   -Spendable:             ([\.0-9]+?) .*$", result[l + 3]).group(1))
                if amount - expect_amount >= -0.001:
                    return True
    else:
        txs = get_chia_txs()
        for tx in txs:
            if tx["sent"] == 0:
                request = '{"transaction_id": "'+tx["name"]+'"}'
                try:
                    memo = subprocess.check_output([CHIA_PATH, "rpc", "wallet", "get_transaction_memo", request]).decode(
                    "utf-8")
                    memo = json.loads(memo)
                    decoded_string = bytes.fromhex(memo[tx["name"][2:]][tx["name"][2:]][0]).decode('utf-8')
                    response = json.loads(decoded_string)
                    if "symbol" in response and response["symbol"] == stock.stock:
                        if "order_id" in response and response["order_id"] > str(stock.last_updated.timestamp()):
                            # The order is created after the last update
                            return True
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
