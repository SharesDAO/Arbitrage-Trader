import calendar
import datetime
import json
import os
import re
import struct
import subprocess
import time
import traceback
from datetime import datetime

import base58
import requests
from solana.rpc.api import Client
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solana.rpc.types import TokenAccountOpts
from solana.rpc.commitment import Commitment
from cachetools import TTLCache, cached
from chia.types.blockchain_format.program import Program
from chia.types.signing_mode import CHIP_0002_SIGN_MESSAGE_PREFIX
from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import calculate_synthetic_secret_key, \
    DEFAULT_HIDDEN_PUZZLE_HASH
from chia_rs import PrivateKey, AugSchemeMPL
from spl.token.instructions import get_associated_token_address

from util.bech32m import encode_puzzle_hash
from constants.constant import PositionStatus, CONFIG, REQUEST_TIMEOUT, StrategyType
from util.db import update_position, get_last_trade, delete_trade
from util.stock import STOCKS

coin_cache = TTLCache(maxsize=100, ttl=600)
price_cache = TTLCache(maxsize=100, ttl=30)
tx_cache = TTLCache(maxsize=100, ttl=30)
balance_cache = TTLCache(maxsize=10, ttl=10)
token_cache = TTLCache(maxsize=10, ttl=10)
cat_cache = TTLCache(maxsize=10, ttl=60)
xch_cache = TTLCache(maxsize=10, ttl=60)
last_checked_tx = {}
CHIA_PATH = "chia"
XCH_MOJO = 1000000000000
CAT_MOJO = 1000
SOLANA_URL = os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
API_KEY = os.getenv("SPACESCAN_API_KEY", "tkn1qqqh2y5ew7qhwd3c3ehcg86d7wlnlamrxxvhddsh2y5ew7qhwqqqjgu2p8")
HEADERS = {
    "x-api-key": API_KEY
}
MAX_RETRIES = 3

def extract_timestamp_from_order_id(order_id: str) -> float:
    """
    Extract timestamp from order_id string.
    Order_id format: {timestamp}{other_content}, e.g., "1770147712COINed0d4c"
    
    Args:
        order_id: Order ID string that starts with a timestamp
        
    Returns:
        Timestamp as float, or 0.0 if extraction fails
    """
    if not order_id:
        return 0.0
    
    # Extract leading numeric part (timestamp)
    match = re.match(r'^(\d+)', order_id)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return 0.0
    return 0.0

def load_xch_txs(xch_json_file):
    # 读取XCH交易数据
    with open(xch_json_file, 'r') as f:
        data = json.load(f)
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

def load_cat_txs(cat_json_file):
    with open(cat_json_file, 'r') as f:
        data = json.load(f)
        cat_txs = {}
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

@cached(xch_cache)
def fetch_xch_txs():
    try:
        url = f"https://pro-api.spacescan.io/address/xch-transaction/{CONFIG['ADDRESS']}?count=200"
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
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
                response_memo = json.loads(decoded_string)
                tx["memo"] = response_memo
            except Exception as e:
                tx["memo"] = {"customer_id": "", "symbol": ""}
        return data["received_transactions"]["transactions"]
    except requests.exceptions.RequestException as e:
        raise Exception(f"API请求失败: {e}")
    except json.JSONDecodeError as e:
        raise Exception(f"JSON解析失败: {e}")

@cached(cat_cache)
def fetch_cat_txs():
    try:
        url = f"https://pro-api.spacescan.io/address/token-transaction/{CONFIG['ADDRESS']}?count=200"
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        
        cat_txs = {}
        if data["status"] != "success":
            raise Exception("Failed to get CAT transactions")
        
        for tx in data["received_transactions"]["transactions"]:
            # Determine if transaction is sent or received
            # Since this is in received_transactions, it's generally a received transaction
            # But check 'from' field: if 'from' matches our address, it's actually sent by us
            if "from" in tx and tx.get("from") == CONFIG.get("ADDRESS"):
                tx["sent"] = 1  # We sent this transaction
            else:
                tx["sent"] = 0  # We received this transaction (default for received_transactions)
            tx["amount"] = tx["token_amount"] * CAT_MOJO
            try:
                if len(tx["memo"][0]) > 81:
                    decoded_string = bytes.fromhex(tx["memo"][0]).decode('utf-8')
                else:
                    decoded_string = bytes.fromhex(tx["memo"][1]).decode('utf-8')
                response_memo = json.loads(decoded_string)
                tx["memo"] = response_memo
            except Exception as e:
                tx["memo"] = {"customer_id": "", "symbol": ""}
            if tx["asset_id"].lower() not in cat_txs:
                cat_txs[tx["asset_id"].lower()] = []
            cat_txs[tx["asset_id"].lower()].append(tx)
        return cat_txs
    except requests.exceptions.RequestException as e:
        raise Exception(f"API请求失败: {e}")
    except json.JSONDecodeError as e:
        raise Exception(f"JSON解析失败: {e}")

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
    url = f"{CONFIG['VAULT_HOST']}:8888/transactions"
    # Request with parameters
    params = {
        "wallet_id": "XCH",
        "end": "30"
    }

    response = requests.post(url, data=json.dumps(params))
    data = response.json()
        
    if "success" not in data or data["success"] != True:
        raise Exception("Failed to get XCH transactions")
    for tx in data["transactions"]:
        tx["sent"] = 0
        tx["amount"] = int(tx["amount"] * XCH_MOJO)
        try:
            if len(tx["memo"][0]) > 81:
                decoded_string = bytes.fromhex(tx["memo"][0]).decode('utf-8')
            else:
                decoded_string = bytes.fromhex(tx["memo"][1]).decode('utf-8')
            memo = json.loads(decoded_string)
            tx["memo"] = memo
        except Exception as e:
            tx["memo"] = {"customer_id": "", "symbol": ""}
    return data["transactions"]


def get_sol_txs(logger):
    try:
        client = Client(SOLANA_URL)
        last_tx = None if "SOL" not in last_checked_tx else last_checked_tx["SOL"]
        # Get recent confirmed signatures for transactions involving this wallet
        response = client.get_signatures_for_address(
            Pubkey.from_string(CONFIG['ADDRESS']),
            limit=50,
            until=last_tx,
            commitment=Commitment("confirmed")
        )
        
        if not response.value:
            return []
        last_checked_tx["SOL"] = response.value[0].signature
        transactions = []
        
        # For each signature, get the full transaction details
        for sig_info in response.value:
            tx_response = client.get_transaction(
                sig_info.signature,
                commitment=Commitment("confirmed"),
                max_supported_transaction_version=0
            )
            
            if not tx_response.value:
                continue
                
            tx_data = tx_response.value

            if tx_data:
                tx = {
                    "signature": sig_info.signature,
                    "sent": 0,  # Assuming it's received
                    "amount": 0,
                    "memo": None,
                    "timestamp": sig_info.block_time if sig_info.block_time else 0,
                    "slot": sig_info.slot
                }
                # Look through the transaction instructions
                if tx_data.transaction.meta and tx_data.transaction.transaction.message.instructions:
                    message = tx_data.transaction.transaction.message
                    for instruction in message.instructions:
                        # The Memo Program ID
                        if str(message.account_keys[
                                   instruction.program_id_index]) == "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr":
                            # Decode the memo data
                            try:
                                memo_data = base58.b58decode(instruction.data)
                                tx["memo"] = json.loads(memo_data.decode('utf-8'))
                            except Exception as e:
                                continue
                        if str(message.account_keys[
                                   instruction.program_id_index]) == "11111111111111111111111111111111" and len(
                                instruction.data) >= 12:  # System Program ID
                            # First 4 bytes are instruction type
                            parsed_data = base58.b58decode(instruction.data)
                            instruction_type = struct.unpack("<I", parsed_data[0:4])[0]

                            # Check if it's a transfer instruction (type 2)
                            if instruction_type == 2:
                                # Extract lamports from bytes 4-12
                                tx["amount"] = struct.unpack("<Q", parsed_data[4:12])[0]
                if tx["memo"] and "customer_id" in tx["memo"] and tx["amount"] > 0:
                    if "did_id" in tx["memo"]:
                        tx["sent"] = 1
                    else:
                        tx["sent"] = 0
                    transactions.append(tx)
        logger.info(f"Found {len(transactions)} SOL transactions")
        return transactions
    except Exception as e:
        print(f"Failed to get SOL transactions: {str(e)}")
        return []


def get_cat_txs():

    url = f"{CONFIG['VAULT_HOST']}:8888/transactions"

    # Request with parameters
    params = {
        "wallet_id": "CAT",
        "end": "30"
    }
    response = requests.post(url, data=json.dumps(params))
    data = response.json()
        
    if "success" not in data or data["success"] != True:
        raise Exception("Failed to get CAT transactions")
    cat_txs = {}
    for tx in data["transactions"]:
        tx["sent"] = 0
        tx["amount"] = tx["amount"] * CAT_MOJO
        try:
            if len(tx["memo"][0]) > 81:
                decoded_string = bytes.fromhex(tx["memo"][0]).decode('utf-8')
            else:
                decoded_string = bytes.fromhex(tx["memo"][1]).decode('utf-8')
            memo = json.loads(decoded_string)
            tx["memo"] = memo
        except Exception as e:
            tx["memo"] = {"customer_id": "", "symbol": ""}
        if tx["asset_id"].lower() not in cat_txs:
            cat_txs[tx["asset_id"].lower()] = []
        cat_txs[tx["asset_id"].lower()].append(tx)
    return cat_txs


def get_spl_token_txs(logger):
    try:
        client = Client(SOLANA_URL)
        token_txs = {}
        # For each token in the balance, get its transactions
        for stock in CONFIG["TRADING_SYMBOLS"]:
            token_mint = STOCKS[stock["TICKER"]]["asset_id"]
            token_txs[token_mint] = []
            account_pubkey = get_associated_token_address(Pubkey.from_string(CONFIG['ADDRESS']), Pubkey.from_string(token_mint))
            last_tx = None if token_mint not in last_checked_tx else last_checked_tx[token_mint]
            # Get transaction signatures for this token account
            sigs_response = client.get_signatures_for_address(
                account_pubkey,
                limit=50,
                until=last_tx,
                commitment=Commitment("confirmed")
            )

            if not sigs_response.value:
                continue
            last_checked_tx[token_mint] = sigs_response.value[0].signature
            # Process each transaction
            for sig_info in sigs_response.value:
                tx_response = client.get_transaction(
                    sig_info.signature,
                    commitment=Commitment("confirmed"),
                    max_supported_transaction_version=0
                )

                if not tx_response.value:
                    continue

                tx_data = tx_response.value

                # Create a transaction object similar to Chia's CAT transactions


                if tx_data:
                    tx = {
                        "signature": str(sig_info.signature),
                        "sent": 0,  # Assuming it's received
                        "asset_id": token_mint,
                        "amount": 0,
                        "memo": None,
                        "timestamp": sig_info.block_time if sig_info.block_time else 0,
                        "slot": sig_info.slot
                    }
                    # Look through the transaction instructions
                    if tx_data.transaction.meta and tx_data.transaction.transaction.message.instructions:
                        message = tx_data.transaction.transaction.message
                        for instruction in message.instructions:
                            # The Memo Program ID
                            if str(message.account_keys[instruction.program_id_index]) == "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr":
                                # Decode the memo data
                                try:
                                    memo_data = base58.b58decode(instruction.data)
                                    tx["memo"] = json.loads(memo_data.decode('utf-8'))
                                except Exception as e:
                                    continue
                            # Get token amount from token program
                            if str(message.account_keys[instruction.program_id_index]) == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA":
                                # First 4 bytes are instruction type
                                parsed_data = base58.b58decode(instruction.data)
                                # Check if it's a transfer instruction (type 2)
                                tx["amount"] = struct.unpack("<Q", parsed_data[1:9])[0]
                    if tx["memo"] and "customer_id" in tx["memo"] and tx["amount"] > 0:
                        if "did_id" in tx["memo"]:
                            tx["sent"] = 1
                        else:
                            tx["sent"] = 0
                        token_txs[token_mint].append(tx)
        logger.info(f"Found {len(token_txs)} SPL token transactions")
        return token_txs
    except Exception as e:
        logger.error(f"Failed to get SPL token transactions: {str(e)}")
        return {}


def check_pending_positions(traders, logger, update: bool = False):
    token_balance = get_token_balance()
    
    # Define constants at function start
    SOL_LAMPORTS = 1_000_000_000  # 10^9 lamports in 1 SOL
    
    # Get transactions based on blockchain type
    if CONFIG["BLOCKCHAIN"] == "SOLANA":
        crypto_txs = get_sol_txs(logger)
        all_token_txs = get_spl_token_txs(logger)
        logger.info(f"Fetched {len(crypto_txs)} SOL txs,  {len(all_token_txs)} SPL tokens")
        token_divisor = 1_000_000_000
    elif CONFIG["BLOCKCHAIN"] == "CHIA" and update:
        crypto_txs = load_xch_txs(CONFIG["XCH_TX_FILE"])
        all_token_txs = load_cat_txs(CONFIG["CAT_TX_FILE"])
        logger.info(f"Fetched {len(crypto_txs)} XCH txs, {len(all_token_txs)} CAT tokens")
        token_divisor = CAT_MOJO
    elif CONFIG["BLOCKCHAIN"] == "CHIA":
        crypto_txs = fetch_xch_txs()
        all_token_txs = fetch_cat_txs()
        logger.info(f"Fetched {len(crypto_txs)} XCH txs, {len(all_token_txs)} CAT tokens")
        token_divisor = CAT_MOJO
    else:
        # For other blockchains (e.g., EVM), transactions should be handled elsewhere
        logger.warning(f"Unsupported blockchain type: {CONFIG['BLOCKCHAIN']}")
        return False
    
    for trader in traders:
        confirmed = False
        logger.info(f"Checking {trader.stock}, status: {trader.position_status}")
        if trader.position_status == PositionStatus.PENDING_BUY.name:
            if trader.type == StrategyType.DCA:
                # Check if the pending buy is confirmed
                expect_amount = trader.volume
                amount = token_balance[STOCKS[trader.ticker]["asset_id"]]["balance"]
                if amount - expect_amount >= -0.003:
                    trader.position_status = PositionStatus.TRADABLE.name
                    trader.volume = amount
                    update_position(trader)
                    logger.info(f"Buy {trader.stock} confirmed")
                    confirmed = True
            if trader.type == StrategyType.GRID:
                asset_id = STOCKS[trader.ticker]["asset_id"].lower() if CONFIG["BLOCKCHAIN"] == "CHIA" else STOCKS[trader.ticker]["asset_id"]
                if asset_id not in all_token_txs:
                    all_token_txs[asset_id] = []
                token_txs = all_token_txs[asset_id]
                for tx in token_txs:
                    if tx["sent"] == 0:
                        try:
                            if "customer_id" in tx["memo"] and tx["memo"]["customer_id"] == trader.stock:
                                # Check if order_id and status exist (new format)
                                if "order_id" in tx["memo"] and "status" in tx["memo"]:
                                    order_timestamp = extract_timestamp_from_order_id(tx["memo"]["order_id"])
                                    min_timestamp = trader.last_updated.timestamp() - CONFIG["MAX_ORDER_TIME_OFFSET"]
                                    if order_timestamp > min_timestamp:
                                        if tx["memo"]["status"] == "COMPLETED":
                                            trader.position_status = PositionStatus.TRADABLE.name
                                            trader.volume = tx["amount"] / token_divisor
                                            update_position(trader)
                                            logger.info(f"Buy {trader.stock} confirmed")
                                            confirmed = True
                                            break
                        except Exception as e:
                            continue
            # Check if the order is cancelled
            if confirmed:
                continue
            for tx in crypto_txs:
                if tx["sent"] == 0:
                    try:
                        # Check if the order is cancelled
                        logger.debug(f"Checking buy cancellation:{tx['memo']}, ticker: {trader.ticker}, timestamp: {trader.last_updated.timestamp() - CONFIG['MAX_ORDER_TIME_OFFSET']}, type: {trader.type}, stock:{trader.stock}")
                        if "symbol" in tx["memo"] and tx["memo"]["symbol"] == trader.ticker:
                            if "order_id" in tx["memo"]:
                                order_timestamp = extract_timestamp_from_order_id(tx["memo"]["order_id"])
                                min_timestamp = trader.last_updated.timestamp() - CONFIG["MAX_ORDER_TIME_OFFSET"]
                                if order_timestamp > min_timestamp:
                                    if tx["memo"]["status"] == "CANCELLED":
                                        if trader.type == StrategyType.DCA or (
                                                trader.type == StrategyType.GRID and trader.stock == tx["memo"]["customer_id"]):
                                            last_trade = get_last_trade(trader.stock)
                                            if last_trade is None:
                                                logger.warning(f"No last trade found for {trader.stock}, skipping cancellation")
                                                continue
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
                        logger.error(f"Failed to check buy cancellation: {str(e)}")
                        continue
        if trader.position_status == PositionStatus.PENDING_SELL.name or trader.position_status == PositionStatus.PENDING_LIQUIDATION.name:
            # Check if the order is cancelled
            asset_id = STOCKS[trader.ticker]["asset_id"].lower() if CONFIG["BLOCKCHAIN"] == "CHIA" else STOCKS[trader.ticker]["asset_id"]
            if asset_id not in all_token_txs:
                all_token_txs[asset_id] = []
            token_txs = all_token_txs[asset_id]
            for tx in token_txs:
                if tx["sent"] == 0:
                    try:
                        if "symbol" in tx["memo"] and tx["memo"]["symbol"] == trader.ticker:
                            if "order_id" in tx["memo"]:
                                order_timestamp = extract_timestamp_from_order_id(tx["memo"]["order_id"])
                                min_timestamp = trader.last_updated.timestamp() - CONFIG["MAX_ORDER_TIME_OFFSET"]
                                if order_timestamp > min_timestamp:
                                    if tx["memo"]["status"] == "CANCELLED":
                                        if trader.type == StrategyType.DCA or trader.stock == tx["memo"]["customer_id"]:
                                            trader.position_status = PositionStatus.TRADABLE.name
                                            trader.last_updated = datetime.now()
                                            update_position(trader)
                                            last_trade = get_last_trade(trader.stock)
                                            if last_trade is not None:
                                                delete_trade(last_trade[0])
                                            confirmed = True
                                            logger.info(f"Sell {trader.stock} cancelled")
                                            break
                    except Exception as e:
                        continue
            if confirmed:
                continue
            # Check if the order is completed
            for tx in crypto_txs:
                if tx["sent"] == 0:
                    try:
                        if "symbol" in tx["memo"] and tx["memo"]["symbol"] == trader.ticker:
                            # Check if order_id and status exist (new format)
                            if "order_id" in tx["memo"] and "status" in tx["memo"]:
                                logger.debug(
                                    f"Last Update {str(trader.last_updated.timestamp())}, Order: {tx['memo']['order_id']}")
                                order_timestamp = extract_timestamp_from_order_id(tx["memo"]["order_id"])
                                min_timestamp = trader.last_updated.timestamp() - CONFIG["MAX_ORDER_TIME_OFFSET"]
                                if order_timestamp > min_timestamp:
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
                                            logger.info(f"Sell {trader.stock} confirmed (with order_id)")
                                            break
                                        if trader.type == StrategyType.GRID and trader.stock == tx["memo"]["customer_id"]:
                                            # The order is created after the last update
                                            divisor = SOL_LAMPORTS if CONFIG["BLOCKCHAIN"] == "SOLANA" else XCH_MOJO
                                            trader.profit += tx["amount"]/divisor - trader.total_cost
                                            trader.position_status = PositionStatus.TRADABLE.name
                                            trader.volume = 0
                                            trader.buy_count = trader.buy_count+1
                                            trader.last_buy_price = 0
                                            trader.total_cost = 0
                                            trader.avg_price = 0
                                            trader.current_price = 0
                                            trader.last_updated = datetime.now()
                                            update_position(trader)
                                            logger.info(f"Sell {trader.stock} confirmed (with order_id)")
                                            break
                            # Fallback: if no order_id/status but symbol and customer_id match, confirm based on transaction time
                            elif "order_id" not in tx["memo"] or "status" not in tx["memo"]:
                                # Check if customer_id matches for GRID strategy
                                if trader.type == StrategyType.GRID and "customer_id" in tx["memo"] and trader.stock == tx["memo"]["customer_id"]:
                                    tx_time = tx.get("time")
                                    if tx_time:
                                        try:
                                            tx_timestamp = datetime.fromisoformat(tx_time.replace("Z", "+00:00")).timestamp()
                                            min_timestamp = trader.last_updated.timestamp() - CONFIG["MAX_ORDER_TIME_OFFSET"]
                                            if tx_timestamp > min_timestamp:
                                                divisor = SOL_LAMPORTS if CONFIG["BLOCKCHAIN"] == "SOLANA" else XCH_MOJO
                                                trader.profit += tx["amount"]/divisor - trader.total_cost
                                                trader.position_status = PositionStatus.TRADABLE.name
                                                trader.volume = 0
                                                trader.buy_count = trader.buy_count+1
                                                trader.last_buy_price = 0
                                                trader.total_cost = 0
                                                trader.avg_price = 0
                                                trader.current_price = 0
                                                trader.last_updated = datetime.now()
                                                update_position(trader)
                                                logger.info(f"Sell {trader.stock} confirmed (fallback: no order_id, tx_time={tx_time})")
                                                break
                                        except Exception as e:
                                            logger.debug(f"Failed to parse transaction time: {e}")
                                    else:
                                        # If no transaction time available, confirm anyway if customer_id matches
                                        divisor = SOL_LAMPORTS if CONFIG["BLOCKCHAIN"] == "SOLANA" else XCH_MOJO
                                        trader.profit += tx["amount"]/divisor - trader.total_cost
                                        trader.position_status = PositionStatus.TRADABLE.name
                                        trader.volume = 0
                                        trader.buy_count = trader.buy_count+1
                                        trader.last_buy_price = 0
                                        trader.total_cost = 0
                                        trader.avg_price = 0
                                        trader.current_price = 0
                                        trader.last_updated = datetime.now()
                                        update_position(trader)
                                        logger.info(f"Sell {trader.stock} confirmed (fallback: no order_id, no tx_time)")
                                        break
                                elif trader.type == StrategyType.DCA:
                                    # For DCA, confirm if symbol matches and no order_id/status
                                    tx_time = tx.get("time")
                                    if tx_time:
                                        try:
                                            tx_timestamp = datetime.fromisoformat(tx_time.replace("Z", "+00:00")).timestamp()
                                            min_timestamp = trader.last_updated.timestamp() - CONFIG["MAX_ORDER_TIME_OFFSET"]
                                            if tx_timestamp > min_timestamp:
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
                                                logger.info(f"Sell {trader.stock} confirmed (fallback: no order_id, tx_time={tx_time})")
                                                break
                                        except Exception as e:
                                            logger.debug(f"Failed to parse transaction time: {e}")
                    except Exception as e:
                        logger.error(f"Failed to confirm {trader.stock}: {e}")
                        continue
    return False


def check_order_confirmation(traders, did_id, logger):
    """
    Check if pending orders are confirmed on the exchange using get_user_transactions.
    Uses customer_id and created_date to match local orders with exchange transactions.
    Updates trader based on strategy type (DCA/GRID) and order type (BUY/SELL).
    
    Args:
        traders: List of trader objects with pending orders
        did_id: DID ID in hex format for API authentication
        logger: Logger instance
        
    Returns:
        Number of confirmed orders
    """
    # Import here to avoid circular import
    from util.sharesdao import get_user_transactions
    
    confirmed_count = 0
    
    # Define constants
    SOL_LAMPORTS = 1_000_000_000  # 10^9 lamports in 1 SOL
    
    try:
        # Get all pending traders (BUY or SELL)
        pending_traders = [
            t for t in traders 
            if t.position_status in [PositionStatus.PENDING_BUY.name, PositionStatus.PENDING_SELL.name, PositionStatus.PENDING_LIQUIDATION.name]
        ]
        
        if not pending_traders:
            logger.debug("No pending orders to check")
            return 0
        
        logger.info(f"Checking {len(pending_traders)} pending orders for confirmation")
        
        # Fetch transactions from exchange for all statuses (1, 2, 3, 4)
        # Status 1: Pending, 2: Processing, 3: Cancelled, 4: Executed
        all_transactions = []
        for status in [3, 4]:
            try:
                transactions = get_user_transactions(
                    did_id=did_id,
                    status=status,
                    start_index=0,
                    num_of_transactions=200,
                    sort_by_ascending=False,
                    logger=logger
                )
                all_transactions.extend(transactions)
            except Exception as e:
                logger.warning(f"Failed to fetch transactions with status {status}: {e}")
                continue
        
        logger.info(f"Retrieved {len(all_transactions)} transactions from exchange")
        
        # Match each pending trader with exchange transactions
        for trader in pending_traders:
            matched = False
            is_buy_order = trader.position_status == PositionStatus.PENDING_BUY.name
            
            # Try to match by customer_id and created_date
            for tx in all_transactions:
                try:
                    # Get customer_id from transaction (direct field)
                    tx_customer_id = tx.get("customer_id")
                    if tx_customer_id is None:
                        continue
                    
                    # Get order side (BUY/SELL) from transaction type
                    # type % 2 == 1 means BUY, otherwise SELL
                    tx_type = tx.get("type")
                    if tx_type is None:
                        continue
                    
                    # Determine side from type: type % 2 == 1 is BUY, otherwise SELL
                    tx_is_buy = (tx_type % 2 == 1)
                    
                    # Get created_date from transaction (int timestamp)
                    tx_created_date = tx.get("created_date")
                    
                    # Match customer_id
                    if tx_customer_id != trader.stock:
                        continue
                    
                    # Match order side (BUY/SELL) using type
                    if tx_is_buy != is_buy_order:
                        continue
                    
                    # Match created_date (compare timestamps)
                    # Convert trader.last_updated to timestamp for comparison
                    trader_timestamp = trader.last_updated.timestamp() if hasattr(trader.last_updated, 'timestamp') else None
                    
                    if tx_created_date is not None:
                        # created_date is an int timestamp (seconds since epoch)
                        try:
                            tx_timestamp = float(tx_created_date)
                            
                            # Allow small time difference (within 60 seconds) for matching
                            if trader_timestamp and abs(tx_timestamp - trader_timestamp) > 60:
                                continue
                        except Exception as e:
                            logger.debug(f"Failed to parse created_date for transaction {tx}: {e}")
                            # If date parsing fails, still try to match by customer_id only
                            pass
                    
                    # Found a match, check status
                    tx_status = tx.get("status")
                    if tx_status is None:
                        continue
                    
                    matched = True
                    
                    # Get actual received amount from "request" field (direct field, string type)
                    tx_request = tx.get("request")
                    
                    # Status 4: Executed
                    if tx_status == 4:
                        logger.info(f"Order confirmed (executed) for {trader.stock}, side: {'BUY' if is_buy_order else 'SELL'}")
                        
                        if is_buy_order:
                            # Handle BUY order confirmation
                            if trader.type == StrategyType.DCA:
                                # For DCA, update volume from token balance
                                token_balance = get_token_balance()
                                asset_id = STOCKS[trader.ticker]["asset_id"]
                                if asset_id in token_balance:
                                    trader.volume = token_balance[asset_id]["balance"]
                                trader.position_status = PositionStatus.TRADABLE.name
                                trader.last_updated = datetime.now()
                                update_position(trader)
                                logger.info(f"Buy {trader.stock} confirmed (DCA)")
                            elif trader.type == StrategyType.GRID:
                                # For GRID, update volume from request field
                                if tx_request is not None:
                                    # Determine token divisor based on blockchain
                                    if CONFIG["BLOCKCHAIN"] == "SOLANA":
                                        token_divisor = SOL_LAMPORTS
                                    else:
                                        token_divisor = CAT_MOJO
                                    trader.volume = float(tx_request) / token_divisor
                                trader.position_status = PositionStatus.TRADABLE.name
                                trader.last_updated = datetime.now()
                                update_position(trader)
                                logger.info(f"Buy {trader.stock} confirmed (GRID), volume: {trader.volume}")
                        else:
                            # Handle SELL order confirmation
                            if trader.type == StrategyType.DCA:
                                # For DCA, reset all fields
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
                                logger.info(f"Sell {trader.stock} confirmed (DCA)")
                            elif trader.type == StrategyType.GRID:
                                # For GRID, calculate profit and update
                                if tx_request is not None:
                                    # Determine divisor based on blockchain
                                    if CONFIG["BLOCKCHAIN"] == "SOLANA":
                                        divisor = SOL_LAMPORTS
                                    else:
                                        divisor = XCH_MOJO
                                    received_amount = float(tx_request) / divisor
                                    trader.profit += received_amount - trader.total_cost
                                trader.position_status = PositionStatus.TRADABLE.name
                                trader.volume = 0
                                trader.buy_count = trader.buy_count + 1
                                trader.last_buy_price = 0
                                trader.total_cost = 0
                                trader.avg_price = 0
                                trader.current_price = 0
                                trader.last_updated = datetime.now()
                                update_position(trader)
                                logger.info(f"Sell {trader.stock} confirmed (GRID), profit: {trader.profit}")
                        
                        confirmed_count += 1
                        break
                    
                    # Status 3: Cancelled
                    elif tx_status == 3:
                        logger.info(f"Order cancelled for {trader.stock}, side: {'BUY' if is_buy_order else 'SELL'}")
                        
                        if is_buy_order:
                            # Handle BUY cancellation - rollback volume and cost
                            last_trade = get_last_trade(trader.stock)
                            if last_trade is None:
                                logger.warning(f"No last trade found for {trader.stock}, skipping cancellation rollback")
                            else:
                                trader.volume -= last_trade[4]  # volume
                                trader.total_cost -= last_trade[5]  # crypto_cost
                                if trader.type == StrategyType.DCA:
                                    trader.buy_count -= 1
                                
                                # Delete the trade record
                                delete_trade(last_trade[0])
                                logger.info(f"Deleted trade record for cancelled buy order {trader.stock}")
                                
                                # Update remaining position
                                last_trade = get_last_trade(trader.stock)
                                if last_trade is None or last_trade[2] == 'SELL':
                                    trader.last_buy_price = 0
                                    trader.avg_price = 0
                                    trader.volume = 0
                                    trader.total_cost = 0
                                else:
                                    trader.avg_price = trader.total_cost / trader.volume if trader.volume > 0 else 0
                                    trader.last_buy_price = last_trade[3]  # price
                        else:
                            # Handle SELL cancellation - just delete trade record
                            last_trade = get_last_trade(trader.stock)
                            if last_trade is not None:
                                delete_trade(last_trade[0])
                                logger.info(f"Deleted trade record for cancelled sell order {trader.stock}")
                        
                        trader.position_status = PositionStatus.TRADABLE.name
                        trader.last_updated = datetime.now()
                        update_position(trader)
                        confirmed_count += 1
                        break
                
                except Exception as e:
                    logger.debug(f"Error processing transaction {tx}: {e}")
                    continue
            
            if not matched:
                logger.debug(f"No matching transaction found for {trader.stock}, waiting...")
        
        logger.info(f"Confirmed {confirmed_count} orders out of {len(pending_traders)} pending orders")
        return confirmed_count
        
    except Exception as e:
        logger.error(f"Failed to check order confirmation: {e}")
        return 0


def sync_pending_orders(traders, did_id, logger):
    """
    Sync local pending orders with exchange transactions.
    If a local pending order is not found in exchange pending transactions (status 1, 2),
    mark it as confirmed (assuming it was executed or cancelled).
    
    Args:
        traders: List of trader objects
        did_id: DID ID in hex format for API authentication
        logger: Logger instance
        
    Returns:
        Number of synced orders
    """
    # Import here to avoid circular import
    from util.sharesdao import get_user_transactions
    
    synced_count = 0
    
    try:
        # Get all pending traders (BUY or SELL)
        pending_traders = [
            t for t in traders 
            if t.position_status in [PositionStatus.PENDING_BUY.name, PositionStatus.PENDING_SELL.name, PositionStatus.PENDING_LIQUIDATION.name]
        ]
        
        if not pending_traders:
            logger.info("No pending orders to sync")
            return 0
        
        logger.info(f"Syncing {len(pending_traders)} pending orders with exchange")
        
        # Fetch pending transactions from exchange (status 1: Pending, 2: Processing)
        exchange_pending_txs = []
        for status in [1, 2]:
            try:
                transactions = get_user_transactions(
                    did_id=did_id,
                    status=status,
                    start_index=0,
                    num_of_transactions=200,
                    sort_by_ascending=False,
                    logger=logger
                )
                exchange_pending_txs.extend(transactions)
            except Exception as e:
                logger.warning(f"Failed to fetch transactions with status {status}: {e}")
                continue
        
        logger.info(f"Retrieved {len(exchange_pending_txs)} pending transactions from exchange")
        
        # Create a set of matched customer_ids and created_dates from exchange
        exchange_matches = set()
        for tx in exchange_pending_txs:
            tx_customer_id = tx.get("customer_id")
            tx_created_date = tx.get("created_date")
            if tx_customer_id and tx_created_date is not None:
                exchange_matches.add((tx_customer_id, tx_created_date))
        
        # Check each local pending order
        for trader in pending_traders:
            is_buy_order = trader.position_status == PositionStatus.PENDING_BUY.name
            trader_timestamp = trader.last_updated.timestamp() if hasattr(trader.last_updated, 'timestamp') else None
            
            # Try to find matching transaction in exchange pending transactions
            found_match = False
            for tx in exchange_pending_txs:
                try:
                    tx_customer_id = tx.get("customer_id")
                    if tx_customer_id != trader.stock:
                        continue
                    
                    # Check order type
                    tx_type = tx.get("type")
                    if tx_type is None:
                        continue
                    tx_is_buy = (tx_type % 2 == 1)
                    if tx_is_buy != is_buy_order:
                        continue
                    
                    # Check created_date
                    tx_created_date = tx.get("created_date")
                    if tx_created_date is not None and trader_timestamp:
                        try:
                            tx_timestamp = float(tx_created_date)
                            # Allow small time difference (within 60 seconds) for matching
                            if abs(tx_timestamp - trader_timestamp) <= 60:
                                found_match = True
                                break
                        except Exception as e:
                            logger.debug(f"Failed to parse created_date: {e}")
                    else:
                        # If no created_date, match by customer_id and type only
                        found_match = True
                        break
                except Exception as e:
                    logger.debug(f"Error checking transaction: {e}")
                    continue
            
            # If no match found, mark as confirmed
            if not found_match:
                logger.info(f"Local pending order {trader.stock} not found in exchange pending transactions, marking as confirmed")
                trader.position_status = PositionStatus.TRADABLE.name
                trader.last_updated = datetime.now()
                update_position(trader)
                synced_count += 1
        
        logger.info(f"Synced {synced_count} orders out of {len(pending_traders)} pending orders")
        return synced_count
        
    except Exception as e:
        logger.error(f"Failed to sync pending orders: {e}")
        return 0


@cached(balance_cache)
def get_crypto_balance():
    if CONFIG["BLOCKCHAIN"] == "SOLANA":
        # Use Solana RPC API to get balance
        try:    
            # Get Solana client based on network configuration
            
            client = Client(SOLANA_URL)
            
            # Request balance from Solana RPC
            response = client.get_balance(Pubkey.from_string(CONFIG["ADDRESS"]), Commitment("confirmed"))
            
            if response.value is not None:
                # Convert lamports (10^9) to SOL
                return response.value / 1_000_000_000
            return 0
        except Exception as e:
            print(f"Cannot get SOL balance: {str(e)}")
            return None
    else:
        url = f"{CONFIG['VAULT_HOST']}:8888/balance"
        try:
            response = requests.get(url)
            data = response.json()
            if len(data) > 0:
                return data["XCH"]["balance"]
            else:
                return 0
        except Exception as e:
            print(f"Cannot get XCH balance")
            return None


def call_solana_rpc(method, params=None):
    """
    Call a Solana RPC method using requests.

    Args:
        method (str): RPC method name (e.g., "getTokenAccountsByOwner").
        params (list, optional): Method parameters.

    Returns:
        dict: RPC response JSON.

    Raises:
        requests.exceptions.RequestException: For HTTP or network errors.
        ValueError: For invalid JSON or response format.
    """
    headers = {"Content-Type": "application/json"}
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or []
    }

    try:
        response = requests.post(SOLANA_URL, headers=headers, json=payload, timeout=30)
        response.raise_for_status()  # Raise for 4xx/5xx errors

        try:
            result = response.json()
            if "error" in result:
                raise ValueError(f"RPC error: {result['error']}")
            return result
        except ValueError as e:
            raise ValueError(f"Invalid JSON response: {e}")

    except requests.exceptions.HTTPError as e:
        if response.status_code == 429:
            raise  # Trigger retry
        raise requests.exceptions.RequestException(f"HTTP error: {e}")
    except requests.exceptions.RequestException as e:
        raise requests.exceptions.RequestException(f"Network error: {e}")


@cached(token_cache)
def get_token_balance():
    if CONFIG["BLOCKCHAIN"] == "SOLANA":
        # Use Solana RPC API to get SPL token balances
        try:
            # Get Solana client based on network configuration
            client = Client(SOLANA_URL)
            
            # Get all SPL token accounts owned by this wallet address
            response = call_solana_rpc("getTokenAccountsByOwner",[CONFIG["ADDRESS"],
                                                                  {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                                                                  {"encoding": "jsonParsed"}])
            
            if response['result'] is not None:
                token_balances = {}
                
                for account in response['result']['value']:
                    # Parse token data from the response
                    account_data = account["account"]["data"]["parsed"]["info"]
                    mint = account_data["mint"]  # Token mint address (equivalent to asset_id)
                    amount = int(account_data["tokenAmount"]["amount"])
                    
                    # Format in same structure as Chia tokens for compatibility
                    token_balances[mint] = {
                        "asset_id": mint,
                        "balance": amount / 1_000_000_000,
                    }
                
                return token_balances
            return {}
        except Exception as e:
            print(f"Cannot get Solana token balance: {str(e)}")
            return {}
    else:
        url = f"{CONFIG['VAULT_HOST']}:8888/balance"
        try:
            response = requests.get(url)
            data = response.json()
            if len(data) > 0:
                result = {}
                for t in data.values():
                    if str(t["asset_id"]) != "0":
                        result[t["asset_id"]] = t
                return result
            else:
                return {}
        except Exception as e:
            print(f"Cannot get token balance: {str(e)}")
            return None


def add_token(symbol):
    pass


@cached(price_cache)
def get_crypto_price(logger):
    crypto = "XCH"
    if CONFIG["BLOCKCHAIN"] == "SOLANA":
        crypto = "SOL"
    url = f"https://api.sharesdao.com:8443/util/get_price/{crypto}"
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)

        if response.status_code == 200:
            return response.json()[crypto]
        else:
            logger.error(f"Error: {response.status_code}")
            return None
    except Exception as e:
        print(f"Cannot get {crypto} price")
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
    if CONFIG["BLOCKCHAIN"] == "SOLANA":
        private_key = Keypair.from_bytes(bytes.fromhex(os.environ.get("DID_PRIVATE_KEY", "")))
        signature = private_key.sign_message(message.encode("utf-8"))
        # Convert signature to Base58 string (Solana standard format)
        return str(signature)
    if CONFIG["BLOCKCHAIN"] == "CHIA":
        private_key = PrivateKey.from_bytes(bytes.fromhex(os.environ["DID_PRIVATE_KEY"]))
        synthetic_secret_key = calculate_synthetic_secret_key(private_key, DEFAULT_HIDDEN_PUZZLE_HASH)
        hex_message = Program.to((CHIP_0002_SIGN_MESSAGE_PREFIX, message)).get_tree_hash()
        return str(AugSchemeMPL.sign(synthetic_secret_key, hex_message))
