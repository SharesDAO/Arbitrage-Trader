import datetime
import json
import os
import re
import struct
import subprocess
from datetime import datetime
import time
import base58
import requests
from cachetools import TTLCache, cached
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct

from util.bech32m import encode_puzzle_hash
from constants.constant import PositionStatus, CONFIG, REQUEST_TIMEOUT, StrategyType
from util.db import update_position, get_last_trade, delete_trade
from util.stock import STOCKS
from solana.rpc.api import Client
from solana.rpc.commitment import Commitment
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solana.rpc.types import TokenAccountOpts, TxOpts
from spl.token.instructions import get_associated_token_address
from solders.solders import Pubkey, transfer, Instruction, Message, Transaction, AccountMeta, Signature
from solders.system_program import TransferParams
from spl.memo.constants import MEMO_PROGRAM_ID
from spl.token.constants import TOKEN_PROGRAM_ID


coin_cache = TTLCache(maxsize=100, ttl=600)
price_cache = TTLCache(maxsize=100, ttl=30)
tx_cache = TTLCache(maxsize=1000, ttl=300)  # Increased cache size for block timestamps
token_cache = TTLCache(maxsize=10, ttl=10)
last_checked_tx = {}
block_timestamp_cache = {}  # Cache for block timestamps to avoid repeated RPC calls
CHIA_PATH = "chia"
XCH_MOJO = 1000000000000
CAT_MOJO = 1000
SOLANA_DECIAML = 1000000000
SOLANA_URL = os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

# ERC20 Transfer function signature: transfer(address,uint256)
ERC20_TRANSFER_SIGNATURE = "0xa9059cbb"
# ERC20 Transfer event signature: Transfer(address,address,uint256)
ERC20_TRANSFER_EVENT_SIGNATURE = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def send_asset(address: str, wallet_id: int, ticker: str, request: float, offer: float, logger, cid = "", order_type="LIMIT"):
    try:
        if CONFIG["BLOCKCHAIN"] == "SOLANA":
            if wallet_id == 1:
                return send_sol(address, {"customer_id": cid, "type": order_type, "offer": offer, "request": request}, logger)
            else:
                return send_token(address, {"customer_id": cid, "type": order_type, "offer": offer, "request": request}, STOCKS[ticker]["asset_id"], logger)
        elif CONFIG["BLOCKCHAIN"] == "EVM":
            # For EVM, wallet_id 1 = USDC (ERC20 for buy orders), wallet_id 0 = stock ERC20 token (for sell orders)
            if wallet_id == 1:
                # For buy orders, pass stock token address so it can be included in memo
                stock_token_address = STOCKS[ticker]["asset_id"]
                return send_usdc(address, {"customer_id": cid, "type": order_type, "offer": offer, "request": request}, stock_token_address, logger)
            else:
                # wallet_id 0 means sending stock ERC20 token
                return send_stock_token(address, {"customer_id": cid, "type": order_type, "offer": offer, "request": request}, STOCKS[ticker]["asset_id"], logger)
        elif CONFIG["BLOCKCHAIN"] == "CHIA":
            if wallet_id == 1:
                offer_amount = int(offer * XCH_MOJO)
                request_amount = int(request * CAT_MOJO)
                amount = offer_amount / XCH_MOJO
            else:
                offer_amount = int(offer * CAT_MOJO)
                request_amount = int(request * XCH_MOJO)
                amount = offer_amount / CAT_MOJO
            result = subprocess.check_output(
                [CHIA_PATH, "wallet", "send", f'--fingerprint={CONFIG["WALLET_FINGERPRINT"]}', f'--id={wallet_id}',
                f"--address={address}", f"--amount={amount}", f'--fee={CONFIG["CHIA_TX_FEE"]}', "--reuse", "--override", "-e",
                '{"did_id":"' + CONFIG["DID_HEX"] + '","customer_id":"' + cid + '", "type":"' + order_type.upper() + '", "offer":' + str(offer_amount) + ', "request":' + str(
                    request_amount) + '}']).decode(
                "utf-8")
            if result.find("SUCCESS") > 0 or result.find("INVALID_FEE_TOO_CLOSE_TO_ZERO") > 0:
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


def get_chia_txs(wallet_id=1, num=50):
    global last_checked_tx
    request = '{"wallet_id":' + str(wallet_id) + ', "reverse": true, "type_filter":{"values":[0], "mode":1},"end":' + str(num) + '}'
    result = subprocess.check_output([CHIA_PATH, "rpc", "wallet", "get_transactions", request]).decode("utf-8")
    txs = json.loads(result)["transactions"]
    filtered_txs = []
    for tx in txs:
        if wallet_id in last_checked_tx:
            if tx["name"] != last_checked_tx[wallet_id]:
                filtered_txs.append(tx)
            else:
                break
        else:
            filtered_txs.append(tx)
    txs = filtered_txs
    for tx in txs:
        # Get tx memo
        try:
            request = '{"transaction_id": "' + tx["name"] + '"}'
            memo = subprocess.check_output(
                [CHIA_PATH, "rpc", "wallet", "get_transaction_memo", request],
                stderr=subprocess.DEVNULL).decode(
                "utf-8")
            memo = json.loads(memo)
            if len(memo[tx["name"][2:]][tx["name"][2:]][0])>81:
                decoded_string = bytes.fromhex(memo[tx["name"][2:]][tx["name"][2:]][0]).decode('utf-8')
            else:
                decoded_string = bytes.fromhex(memo[tx["name"][2:]][tx["name"][2:]][1]).decode('utf-8')
            response = json.loads(decoded_string)
            tx["memo"] = response
        except Exception as e:
            tx["memo"] = {"customer_id": "", "symbol": ""}
    if len(txs) > 0:
        last_checked_tx[wallet_id] = txs[0]["name"]

    return txs

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


def get_spl_token_txs(logger):
    try:
        client = Client(SOLANA_URL)
        token_txs = {}
        # For each token in the balance, get its transactions
        for stock in CONFIG["TRADING_SYMBOLS"]:
            ticker = stock if "TICKER" not in stock else stock["TICKER"]
            token_mint = STOCKS[ticker]["asset_id"]
            token_txs[token_mint] = []
            account_pubkey = get_associated_token_address(Pubkey.from_string(CONFIG['ADDRESS']),
                                                          Pubkey.from_string(token_mint))
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
                            if str(message.account_keys[
                                       instruction.program_id_index]) == "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr":
                                # Decode the memo data
                                try:
                                    memo_data = base58.b58decode(instruction.data)
                                    tx["memo"] = json.loads(memo_data.decode('utf-8'))
                                except Exception as e:
                                    continue
                            # Get token amount from token program
                            if str(message.account_keys[
                                       instruction.program_id_index]) == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA":
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


def check_pending_positions(traders, logger):
    token_balance = get_token_balance()
    
    # Get transactions based on blockchain type
    if CONFIG["BLOCKCHAIN"] == "SOLANA":
        crypto_txs = get_sol_txs(logger)
        all_token_txs = get_spl_token_txs(logger)
        logger.info(f"Fetched {len(crypto_txs)} SOL txs.")
        SOL_LAMPORTS = SOLANA_DECIAML  # 10^9 lamports in 1 SOL
        token_divisor = SOLANA_DECIAML
    elif CONFIG["BLOCKCHAIN"] == "EVM":
        # For EVM, we don't need native token transactions since orders use ERC20 tokens (USDC/stock tokens)
        crypto_txs = []  # Skip native token tx fetching for EVM
        all_token_txs = get_erc20_token_txs(logger)
        logger.info(f"Fetched {sum(len(txs) for txs in all_token_txs.values())} ERC20 token txs.")
        # Token divisor will be determined per token (USDC uses CONFIG["USDC_DECIMALS"], stock tokens typically use 18)
        token_divisor = 10**CONFIG.get("USDC_DECIMALS", 18)  # Default to USDC decimals
    else:
        crypto_txs = get_xch_txs()
        all_token_txs = get_cat_txs()
        logger.info(f"Fetched {len(crypto_txs)} XCH txs.")
        token_divisor = CAT_MOJO
    
    for trader in traders:
        confirmed = False
        logger.info(f"Checking {trader.stock}, status: {trader.position_status}")
        if trader.position_status == PositionStatus.PENDING_BUY.name:
            if trader.type == StrategyType.DCA:
                # Check if the pending buy is confirmed
                expect_amount = trader.volume
                asset_id = STOCKS[trader.ticker]["asset_id"]
                if CONFIG["BLOCKCHAIN"] == "EVM":
                    asset_id = asset_id.lower()
                amount = token_balance[asset_id]["balance"]
                if amount - expect_amount >= -0.003:
                    trader.position_status = PositionStatus.TRADABLE.name
                    trader.volume = amount
                    update_position(trader)
                    logger.info(f"Buy {trader.stock} confirmed")
                    confirmed = True
            if trader.type == StrategyType.GRID:
                asset_id = STOCKS[trader.ticker]["asset_id"].lower() if CONFIG["BLOCKCHAIN"] == "CHIA" else STOCKS[trader.ticker]["asset_id"]
                if CONFIG["BLOCKCHAIN"] == "EVM":
                    asset_id = asset_id.lower()
                if asset_id not in all_token_txs:
                    all_token_txs[asset_id] = []
                token_txs = all_token_txs[asset_id]
                for tx in token_txs:
                    if tx["sent"] == 0:
                        try:
                            if "customer_id" in tx["memo"] and tx["memo"]["customer_id"] == trader.stock:
                                if "order_id" in tx["memo"] and tx["memo"]["order_id"] > str(
                                        trader.last_updated.timestamp() - CONFIG["MAX_ORDER_TIME_OFFSET"]):
                                    if tx["memo"]["status"] == "COMPLETED":
                                        trader.position_status = PositionStatus.TRADABLE.name
                                        # Determine divisor based on token type
                                        if CONFIG["BLOCKCHAIN"] == "EVM":
                                            # For EVM, check if it's USDC or stock token
                                            if asset_id.lower() == CONFIG["USDC_ADDRESS"].lower():
                                                tx_divisor = 10**CONFIG["USDC_DECIMALS"]
                                            else:
                                                tx_divisor = 10**18  # Stock tokens typically use 18 decimals
                                        else:
                                            tx_divisor = token_divisor
                                        trader.volume = tx["amount"] / tx_divisor
                                        update_position(trader)
                                        logger.info(f"Buy {trader.stock} confirmed")
                                        confirmed = True
                                        break
                        except Exception as e:
                            continue
            # Check if the order is cancelled
            if confirmed:
                continue
            # For EVM, buy orders use USDC (ERC20), so check USDC and stock token transactions instead of native token
            if CONFIG["BLOCKCHAIN"] == "EVM":
                # Check USDC transactions for cancellation
                usdc_address = CONFIG["USDC_ADDRESS"].lower()
                if usdc_address in all_token_txs:
                    for tx in all_token_txs[usdc_address]:
                        if tx["sent"] == 0:
                            try:
                                if tx["memo"] and "symbol" in tx["memo"] and tx["memo"]["symbol"] == trader.ticker:
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
                                logger.error(f"Failed to check buy cancellation: {str(e)}")
                                continue
            else:
                # For non-EVM chains, check native token transactions
                for tx in crypto_txs:
                    if tx["sent"] == 0:
                        try:
                            # Check if the order is cancelled
                            logger.info(f"Checking buy cancellation:{tx['memo']}, ticker: {trader.ticker}, timestamp: {trader.last_updated.timestamp() - CONFIG['MAX_ORDER_TIME_OFFSET']}, type: {trader.type}, stock:{trader.stock}")
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
                            logger.error(f"Failed to check buy cancellation: {str(e)}")
                            continue
        if trader.position_status == PositionStatus.PENDING_SELL.name or trader.position_status == PositionStatus.PENDING_LIQUIDATION.name:
            # Check if the order is cancelled
            asset_id = STOCKS[trader.ticker]["asset_id"].lower() if CONFIG["BLOCKCHAIN"] == "CHIA" else STOCKS[trader.ticker]["asset_id"]
            if CONFIG["BLOCKCHAIN"] == "EVM":
                asset_id = asset_id.lower()
            if asset_id not in all_token_txs:
                all_token_txs[asset_id] = []
            token_txs = all_token_txs[asset_id]
            for tx in token_txs:
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
            # For EVM, sell orders receive USDC (ERC20), so check USDC transactions instead of native token
            if CONFIG["BLOCKCHAIN"] == "EVM":
                # Check USDC transactions for completion
                usdc_address = CONFIG["USDC_ADDRESS"].lower()
                if usdc_address in all_token_txs:
                    for tx in all_token_txs[usdc_address]:
                        if tx["sent"] == 0:
                            try:
                                if tx["memo"] and "symbol" in tx["memo"] and tx["memo"]["symbol"] == trader.ticker:
                                    logger.debug(
                                        f"Last Update {str(trader.last_updated.timestamp())}, Order: {tx['memo'].get('order_id', 'N/A')}")
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
                                                usdc_decimals = CONFIG["USDC_DECIMALS"]
                                                divisor = 10**usdc_decimals
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
                                                logger.info(f"Sell {trader.stock} confirmed")
                                                break
                            except Exception as e:
                                logger.error(f"Failed to confirm {trader.stock}: {e}")
                                continue
            else:
                # For non-EVM chains, check native token transactions
                for tx in crypto_txs:
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
                                            if CONFIG["BLOCKCHAIN"] == "SOLANA":
                                                divisor = SOL_LAMPORTS
                                            else:
                                                divisor = XCH_MOJO
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
                                            logger.info(f"Sell {trader.stock} confirmed")
                                            break
                        except Exception as e:
                            logger.error(f"Failed to confirm {trader.stock}: {e}")
                            continue
    return False


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
                return response.value / SOLANA_DECIAML
            return 0
        except Exception as e:
            print(f"Cannot get SOL balance: {str(e)}")
            return 0
    elif CONFIG["BLOCKCHAIN"] == "EVM":
        # Get native token balance (ETH/BNB) for EVM chain
        try:
            w3 = get_web3()
            address = Web3.to_checksum_address(CONFIG["ADDRESS"])
            balance_wei = w3.eth.get_balance(address)
            # Convert from wei to native token (18 decimals)
            return balance_wei / 10**18
        except Exception as e:
            print(f"Cannot get EVM native token balance: {str(e)}")
            return 0
    else:
        wallet_name = "Chia Wallet"
        try:
            result = subprocess.check_output(
                [CHIA_PATH, "wallet", "show", f"--fingerprint={CONFIG['WALLET_FINGERPRINT']}"]).decode(
                "utf-8").split("\n")
            for l in range(len(result)):
                if result[l].find(f"{wallet_name}:") >= 0:
                    amount = float(re.search(r"^   -Spendable:             ([\.0-9]+?) .*$", result[l + 3]).group(1))
                    return amount
            return 0
        except Exception as e:
            print(f"Cannot get XCH balance")
            return 0


@cached(token_cache)
def get_token_balance():
    if CONFIG["BLOCKCHAIN"] == "SOLANA":
        # Use Solana RPC API to get SPL token balances
        try:

            # Get all SPL token accounts owned by this wallet address
            response = call_solana_rpc("getTokenAccountsByOwner", [CONFIG["ADDRESS"],
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
                        "balance": amount / SOLANA_DECIAML,
                    }

                return token_balances
            return {}
        except Exception as e:
            print(f"Cannot get Solana token balance: {str(e)}")
            return {}
    elif CONFIG["BLOCKCHAIN"] == "EVM":
        # Get ERC20 token balances for EVM chain
        try:
            w3 = get_web3()
            address = Web3.to_checksum_address(CONFIG["ADDRESS"])
            token_balances = {}
            
            # Get USDC balance
            usdc_address = Web3.to_checksum_address(CONFIG["USDC_ADDRESS"])
            usdc_contract = w3.eth.contract(address=usdc_address, abi=get_erc20_abi())
            usdc_balance = usdc_contract.functions.balanceOf(address).call()
            usdc_decimals = CONFIG["USDC_DECIMALS"]
            token_balances[usdc_address.lower()] = {
                "asset_id": usdc_address,
                "balance": usdc_balance / (10 ** usdc_decimals),
            }
            
            # Get stock token balances
            for stock in CONFIG.get("TRADING_SYMBOLS", []):
                ticker = stock if isinstance(stock, str) else stock.get("TICKER")
                if ticker and ticker in STOCKS:
                    token_mint = STOCKS[ticker]["asset_id"]
                    if token_mint:
                        try:
                            token_address = Web3.to_checksum_address(token_mint)
                            token_contract = w3.eth.contract(address=token_address, abi=get_erc20_abi())
                            balance = token_contract.functions.balanceOf(address).call()
                            # Stock tokens typically use 18 decimals
                            token_balances[token_mint.lower()] = {
                                "asset_id": token_mint,
                                "balance": balance / (10 ** 18),
                            }
                        except Exception as e:
                            continue
            
            return token_balances
        except Exception as e:
            print(f"Cannot get EVM token balance: {str(e)}")
            return {}
    else:
        url = f"https://api.spacescan.io/address/token-balance/{CONFIG['ADDRESS']}"
        try:
            response = requests.get(url)
            data = response.json()
            if data["status"] == "success":
                return {t["asset_id"]: t for t in data["data"]}
            else:
                return {}
        except Exception as e:
            print(f"Cannot get token balance")
            return None

def send_sol(address: str, order, logger):
    try:
        client = Client(SOLANA_URL)
        private_key = Keypair.from_base58_string(os.environ.get("DID_PRIVATE_KEY", ""))
        sender_pubkey = private_key.pubkey()
        offer = int(order["offer"] * SOLANA_DECIAML)
        request = int(order["request"] * SOLANA_DECIAML)
        memo = "{"+f'"did_id":"{CONFIG["DID_HEX"]}","customer_id":"{order["customer_id"]}","type":"{("LIMIT" if "type" not in order else order["type"])}","offer":{offer},"request":{request}'+"}"
        # If wallet does not have enough SOL, skip
        balance = get_crypto_balance()
        if balance - offer / SOLANA_DECIAML < 0.008:
            logger.error(f"Insufficient balance {balance}/{offer / SOLANA_DECIAML}, skipping...")
            return False
        # 获取接收者的公钥
        recipient_pubkey = Pubkey.from_string(address)

        # Add transfer instruction
        tx_ix = transfer(
            TransferParams(
                from_pubkey=sender_pubkey,
                to_pubkey=recipient_pubkey,
                lamports=offer
            )
        )

        # Add memo instruction
        memo_data = memo.encode("utf-8")

        # Create and add the memo instruction

        memo_instruction = Instruction(
            program_id=MEMO_PROGRAM_ID,
            accounts=[],
            data=memo_data
        )
        recent_blockhash = client.get_latest_blockhash().value.blockhash

        transaction = Transaction.new_signed_with_payer(
            [tx_ix, memo_instruction],
            sender_pubkey,
            [private_key],
            recent_blockhash
        )
        result = client.send_transaction(transaction, opts=TxOpts(skip_confirmation=False, preflight_commitment=Commitment("confirmed"), max_retries=5)).value
        logger.info(f"Sent {offer} SOL to {address} with memo: '{memo}', signature: {result}")
        return True
    except Exception as e:
        logger.error(f"Error sending transaction: {e}")
        return False


def create_transfer_token_instruction(
        source: Pubkey,
        destination: Pubkey,
        owner: Pubkey,
        amount: int,
) -> Instruction:
    """Create a token transfer instruction"""
    keys = [
        AccountMeta(source, False, True),
        AccountMeta(destination, False, True),
        AccountMeta(owner, True, False),
    ]

    # Transfer instruction data: [3, amount]
    # 3 is the instruction index for Transfer in the SPL Token program
    data = bytes([3]) + amount.to_bytes(8, byteorder="little")

    return Instruction(
        program_id=TOKEN_PROGRAM_ID,
        accounts=keys,
        data=data,
    )


def send_token(address: str, order, token_mint: str, logger):
    try:
        client = Client(SOLANA_URL)
        private_key = Keypair.from_base58_string(os.environ.get("DID_PRIVATE_KEY", ""))
        sender_pubkey = private_key.pubkey()
        mint_pubkey = Pubkey.from_string(token_mint)
        offer = int(order["offer"] * SOLANA_DECIAML)
        request = int(order["request"] * SOLANA_DECIAML)
        memo = "{"+f'"did_id":"{CONFIG["DID_HEX"]}","customer_id":"{order["customer_id"]}","type":"{("LIMIT" if "type" not in order else order["type"])}","offer":{offer},"request":{request}'+"}"
        # Get the recipient's public key
        recipient_pubkey = Pubkey.from_string(address)
        token_balance = get_token_balance()
        if token_balance[token_mint]["balance"] - offer / SOLANA_DECIAML < -0.0001:
            logger.error(f"Insufficient balance {token_balance[token_mint]['balance']}/{offer / SOLANA_DECIAML}, skipping...")
            return False
        # Add transfer instruction
        tx_ix = create_transfer_token_instruction(
            source=get_associated_token_address(sender_pubkey, mint_pubkey),
            destination=get_associated_token_address(recipient_pubkey, mint_pubkey),
            owner=sender_pubkey,
            amount=offer,
        )

        # Add memo instruction
        memo_data = memo.encode("utf-8")

        # Create and add the memo instruction

        memo_instruction = Instruction(
            program_id=MEMO_PROGRAM_ID,
            accounts=[],
            data=memo_data
        )
        recent_blockhash = client.get_latest_blockhash().value.blockhash

        transaction = Transaction.new_signed_with_payer(
            [tx_ix, memo_instruction],
            sender_pubkey,
            [private_key],
            recent_blockhash
        )
        result = client.send_transaction(transaction, opts=TxOpts(skip_confirmation=False, preflight_commitment=Commitment("confirmed"), max_retries=5)).value
        logger.info(f"Sent {offer} SOL to {address} with memo: '{memo}', signature: {result}")
        return True
    except Exception as e:
        logger.error(f"Error sending transaction: {e}")
        return False


def add_token(symbol):
    result = subprocess.check_output([CHIA_PATH, "wallet", "add_token", f"--fingerprint={CONFIG['WALLET_FINGERPRINT']}",
                                      f"--asset-id={STOCKS[symbol]['asset_id']}", f"--token-name={symbol}"]).decode(
        "utf-8")
    if result.find("Successfully added") >= 0:
        return int(re.search(r"^Successfully added.*wallet id ([\.\d]+?) .*$", result).group(1))
    elif result.find("Successfully renamed") >= 0:
        return int(re.search(r"^Successfully renamed.*wallet_id ([\.\d]+?) .*$", result).group(1))


@cached(price_cache)
def get_crypto_price(logger):
    """
    Get the current price of the cryptocurrency (XCH, SOL, or USDC)
    """
    currency = CONFIG.get("CURRENCY", "XCH")
    
    # USDC is a stablecoin, price is approximately 1.0
    if currency == "USDC":
        try:
            url = f"https://api.sharesdao.com:8443/util/get_price/{currency}"
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
            if response.status_code == 200:
                return response.json().get(currency, 1.0)
            else:
                logger.warning(f"Failed to get USDC price from API, using default 1.0")
                return 1.0
        except Exception as e:
            logger.warning(f"Cannot get USDC price: {str(e)}, using default 1.0")
            return 1.0
    
    url = f"https://api.sharesdao.com:8443/util/get_price/{currency}"
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)

        if response.status_code == 200:
            return response.json()[currency]
        else:
            logger.error(f"Error: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Cannot get {currency} price: {str(e)}")
        return None



def sign_message(did, message):
    try:
        if CONFIG["BLOCKCHAIN"] == "CHIA":
            did_id = encode_puzzle_hash(did, "did:chia:")
            response = subprocess.check_output(
                [CHIA_PATH, "rpc", "wallet", "sign_message_by_id", '{"id":"' + did_id + '", "message":"' + message + '"}'],
                stderr=subprocess.DEVNULL).decode("utf-8")
            signature = json.loads(response)
            return signature["signature"]
        elif CONFIG["BLOCKCHAIN"] == "SOLANA":
            private_key = Keypair.from_base58_string(os.environ.get("DID_PRIVATE_KEY", ""))
            return private_key.sign_message(message.encode("utf-8")).to_bytes().hex()
        elif CONFIG["BLOCKCHAIN"] == "EVM":
            private_key = os.environ.get("EVM_PRIVATE_KEY", "")
            if not private_key:
                raise ValueError("EVM_PRIVATE_KEY environment variable not set")
            # Remove 0x prefix if present
            if private_key.startswith("0x"):
                private_key = private_key[2:]
            account = Account.from_key(private_key)
            # Encode message using encode_defunct and sign it
            encoded_message = encode_defunct(text=message)
            signed_message = account.sign_message(encoded_message)
            return signed_message.signature.hex()
    except Exception as e:
        print(f"Cannot sign message {message} with DID {did}")
        raise e


# EVM Functions

def get_web3():
    """Get Web3 instance for the configured EVM chain"""
    if CONFIG["BLOCKCHAIN"] != "EVM":
        raise ValueError("Not an EVM blockchain")
    return Web3(Web3.HTTPProvider(CONFIG["RPC_URL"]))


def send_usdc(address: str, order, token_address: str, logger):
    """Send USDC (ERC20) transaction on EVM chain"""
    try:
        w3 = get_web3()
        private_key = os.environ.get("EVM_PRIVATE_KEY", "")
        if not private_key:
            raise ValueError("EVM_PRIVATE_KEY environment variable not set")
        if private_key.startswith("0x"):
            private_key = private_key[2:]
        
        account = Account.from_key(private_key)
        sender_address = account.address
        
        usdc_address = Web3.to_checksum_address(CONFIG["USDC_ADDRESS"])
        recipient_address = Web3.to_checksum_address(address)
        stock_token_address = Web3.to_checksum_address(token_address)
        
        # Convert amount to wei (USDC has 6 decimals for most chains, 18 for BSC)
        usdc_decimals = CONFIG["USDC_DECIMALS"]
        offer_amount = int(order["offer"] * (10 ** usdc_decimals))
        request_amount = int(order["request"] * (10 ** usdc_decimals))
        
        # Check USDC balance
        usdc_contract = w3.eth.contract(address=usdc_address, abi=get_erc20_abi())
        balance = usdc_contract.functions.balanceOf(sender_address).call()
        if balance < offer_amount:
            logger.error(f"Insufficient USDC balance {balance}/{offer_amount}, skipping...")
            return False
        
        # Build memo JSON with token_address
        memo = json.dumps({
            "did_id": CONFIG["DID_HEX"],
            "customer_id": order["customer_id"],
            "type": order.get("type", "LIMIT"),
            "offer": offer_amount,
            "request": request_amount,
            "token_address": stock_token_address
        })
        
        # Build transfer transaction
        nonce = w3.eth.get_transaction_count(sender_address)
        gas_price = w3.eth.gas_price
        
        # Standard ERC20 transfer - memo cannot be included in transfer data
        # The memo will need to be tracked separately or included in an event
        transfer_data = usdc_contract.encodeABI(fn_name='transfer', args=[recipient_address, offer_amount])
        
        transaction = {
            'to': usdc_address,
            'data': transfer_data,
            'gas': 200000,
            'gasPrice': gas_price,
            'nonce': nonce,
            'chainId': CONFIG["CHAIN_ID"]
        }
        
        # Sign and send transaction
        signed_txn = w3.eth.account.sign_transaction(transaction, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed_txn.rawTransaction)
        
        # Wait for transaction receipt
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        
        logger.info(f"Sent {offer_amount / (10 ** usdc_decimals)} USDC to {address} with memo: '{memo}', tx_hash: {tx_hash.hex()}")
        # Note: Memo information is tracked separately since ERC20 transfers don't support memo directly
        return True
    except Exception as e:
        logger.error(f"Error sending USDC transaction: {e}")
        return False


def send_stock_token(address: str, order, token_mint: str, logger):
    """Send stock ERC20 token transaction on EVM chain (for sell orders)"""
    try:
        w3 = get_web3()
        private_key = os.environ.get("EVM_PRIVATE_KEY", "")
        if not private_key:
            raise ValueError("EVM_PRIVATE_KEY environment variable not set")
        if private_key.startswith("0x"):
            private_key = private_key[2:]
        
        account = Account.from_key(private_key)
        sender_address = account.address
        
        token_address = Web3.to_checksum_address(token_mint)
        recipient_address = Web3.to_checksum_address(address)
        
        # Stock tokens typically use 18 decimals
        token_decimals = 18
        offer_amount = int(order["offer"] * (10 ** token_decimals))
        request_amount = int(order["request"] * (10 ** token_decimals))
        
        # Check token balance
        token_contract = w3.eth.contract(address=token_address, abi=get_erc20_abi())
        balance = token_contract.functions.balanceOf(sender_address).call()
        if balance < offer_amount:
            logger.error(f"Insufficient stock token balance {balance}/{offer_amount}, skipping...")
            return False
        
        # Build memo JSON with token_address
        memo = json.dumps({
            "did_id": CONFIG["DID_HEX"],
            "customer_id": order["customer_id"],
            "type": order.get("type", "LIMIT"),
            "offer": offer_amount,
            "request": request_amount,
            "token_address": token_address
        })
        
        # Build transfer transaction
        nonce = w3.eth.get_transaction_count(sender_address)
        gas_price = w3.eth.gas_price
        
        # Standard ERC20 transfer
        transfer_data = token_contract.encodeABI(fn_name='transfer', args=[recipient_address, offer_amount])
        
        transaction = {
            'to': token_address,
            'data': transfer_data,
            'gas': 200000,
            'gasPrice': gas_price,
            'nonce': nonce,
            'chainId': CONFIG["CHAIN_ID"]
        }
        
        # Sign and send transaction
        signed_txn = w3.eth.account.sign_transaction(transaction, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed_txn.rawTransaction)
        
        # Wait for transaction receipt
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        
        logger.info(f"Sent {offer_amount / (10 ** token_decimals)} stock tokens to {address} with memo: '{memo}', tx_hash: {tx_hash.hex()}")
        # Note: Memo information is tracked separately since ERC20 transfers don't support memo directly
        return True
    except Exception as e:
        logger.error(f"Error sending stock token transaction: {e}")
        return False


def send_native_token(address: str, order, logger):
    """Send native token (ETH/BNB) transaction on EVM chain"""
    try:
        w3 = get_web3()
        private_key = os.environ.get("EVM_PRIVATE_KEY", "")
        if not private_key:
            raise ValueError("EVM_PRIVATE_KEY environment variable not set")
        if private_key.startswith("0x"):
            private_key = private_key[2:]
        
        account = Account.from_key(private_key)
        sender_address = account.address
        recipient_address = Web3.to_checksum_address(address)
        
        # Convert amount to wei (18 decimals for ETH/BNB)
        offer_amount = int(order["offer"] * 10**18)
        request_amount = int(order["request"] * 10**18)
        
        # Check balance
        balance = w3.eth.get_balance(sender_address)
        if balance < offer_amount:
            logger.error(f"Insufficient native token balance {balance}/{offer_amount}, skipping...")
            return False
        
        # Build memo JSON
        memo = json.dumps({
            "did_id": CONFIG["DID_HEX"],
            "customer_id": order["customer_id"],
            "type": order.get("type", "LIMIT"),
            "offer": offer_amount,
            "request": request_amount
        })
        
        # Encode memo in transaction data
        memo_bytes = memo.encode('utf-8')
        memo_length = len(memo_bytes).to_bytes(4, byteorder='big')
        memo_data = memo_length.hex() + memo_bytes.hex()
        
        nonce = w3.eth.get_transaction_count(sender_address)
        gas_price = w3.eth.gas_price
        
        transaction = {
            'to': recipient_address,
            'value': offer_amount,
            'data': '0x' + memo_data,
            'gas': 100000,
            'gasPrice': gas_price,
            'nonce': nonce,
            'chainId': CONFIG["CHAIN_ID"]
        }
        
        # Sign and send transaction
        signed_txn = w3.eth.account.sign_transaction(transaction, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed_txn.rawTransaction)
        
        # Wait for transaction receipt
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        
        logger.info(f"Sent {offer_amount / 10**18} {CONFIG['NATIVE_SYMBOL']} to {address} with memo: '{memo}', tx_hash: {tx_hash.hex()}")
        return True
    except Exception as e:
        logger.error(f"Error sending native token transaction: {e}")
        return False


def get_erc20_abi():
    """Get minimal ERC20 ABI for balanceOf and transfer"""
    return [
        {
            "constant": True,
            "inputs": [{"name": "_owner", "type": "address"}],
            "name": "balanceOf",
            "outputs": [{"name": "balance", "type": "uint256"}],
            "type": "function"
        },
        {
            "constant": False,
            "inputs": [
                {"name": "_to", "type": "address"},
                {"name": "_value", "type": "uint256"}
            ],
            "name": "transfer",
            "outputs": [{"name": "", "type": "bool"}],
            "type": "function"
        },
        {
            "anonymous": False,
            "inputs": [
                {"indexed": True, "name": "from", "type": "address"},
                {"indexed": True, "name": "to", "type": "address"},
                {"indexed": False, "name": "value", "type": "uint256"}
            ],
            "name": "Transfer",
            "type": "event"
        }
    ]


def decode_memo_from_data(data: str):
    """Decode memo from transaction data (only for native token transfers)"""
    try:
        if not data or len(data) < 8:
            return None
        
        # Remove 0x prefix
        if data.startswith("0x"):
            data = data[2:]
        
        # For native token transfer, memo is directly in data
        # First 4 bytes (8 hex chars) are length
        if len(data) >= 8:
            memo_length = int(data[:8], 16)
            if memo_length > 0 and len(data) >= 8 + memo_length * 2:
                memo_hex = data[8:8 + memo_length * 2]
                if memo_hex:
                    memo_bytes = bytes.fromhex(memo_hex)
                    return json.loads(memo_bytes.decode('utf-8'))
    except Exception as e:
        return None
    return None


def get_evm_txs(logger):
    """Get native token (ETH/BNB) transactions for EVM chain"""
    # For EVM, orders use ERC20 tokens, so we don't need native token transactions
    # This function is kept for compatibility but returns empty list for EVM
    return []


def get_erc20_token_txs(logger):
    """Get ERC20 token (USDC and stock tokens) transactions for EVM chain"""
    try:
        w3 = get_web3()
        address = Web3.to_checksum_address(CONFIG["ADDRESS"])
        usdc_address = Web3.to_checksum_address(CONFIG["USDC_ADDRESS"])
        token_txs = {}
        
        # Get transactions for USDC and stock tokens
        tokens_to_check = [usdc_address]
        for stock in CONFIG.get("TRADING_SYMBOLS", []):
            ticker = stock if isinstance(stock, str) else stock.get("TICKER")
            if ticker and ticker in STOCKS:
                token_mint = STOCKS[ticker]["asset_id"]
                if token_mint:
                    tokens_to_check.append(Web3.to_checksum_address(token_mint))
        
        current_block = w3.eth.block_number
        # Reduce block range for better performance (1000 blocks instead of 10000)
        # This covers approximately 2-3 hours of transactions on most EVM chains
        from_block = max(0, current_block - 1000)
        
        # Create filter for Transfer events
        for token_address in tokens_to_check:
            token_txs[token_address.lower()] = []
            last_tx = last_checked_tx.get(token_address.lower())
            
            # Create filter for Transfer events to this address
            try:
                transfer_filter = w3.eth.filter({
                    "fromBlock": from_block,
                    "toBlock": "latest",
                    "address": token_address,
                    "topics": [
                        ERC20_TRANSFER_EVENT_SIGNATURE,
                        None,  # from
                        "0x" + "0" * 24 + address[2:].lower()  # to (this address)
                    ]
                })
                
                events = transfer_filter.get_all_entries()
            except Exception as e:
                logger.warning(f"Failed to create filter for {token_address}: {e}")
                continue
            
            # Stop early if we found the last checked transaction
            found_last_tx = False
            events_to_process = []
            
            # First pass: collect events until we find last_tx
            for event in events:
                if last_tx and event['transactionHash'].hex() == last_tx:
                    found_last_tx = True
                    break
                events_to_process.append(event)
            
            # Process events in reverse order (newest first) and stop after processing a reasonable number
            # Limit to last 50 events for performance
            events_to_process = events_to_process[:50]
            
            # Batch process events
            for event in reversed(events_to_process):
                tx_hash = event['transactionHash']
                try:
                    # Get receipt (faster than full transaction)
                    tx_receipt = w3.eth.get_transaction_receipt(tx_hash)
                    
                    # Get block info only once per block (cache block timestamps)
                    block_number = tx_receipt.blockNumber
                    block_cache_key = f"block_{block_number}"
                    if block_cache_key not in tx_cache:
                        try:
                            block = w3.eth.get_block(block_number, full_transactions=False)
                            tx_cache[block_cache_key] = block.timestamp
                        except:
                            tx_cache[block_cache_key] = int(time.time())
                    block_timestamp = tx_cache[block_cache_key]
                    
                    # Decode Transfer event
                    amount = int(event['data'].hex(), 16)
                    
                    tx_obj = {
                        "signature": tx_hash.hex(),
                        "sent": 0,
                        "asset_id": token_address.lower(),
                        "amount": amount,
                        "memo": None,  # Memo is not in ERC20 transfers, handled separately
                        "timestamp": block_timestamp,
                        "block_number": block_number
                    }
                    
                    # Add transaction if amount > 0
                    if tx_obj["amount"] > 0:
                        token_txs[token_address.lower()].append(tx_obj)
                except Exception as e:
                    logger.debug(f"Failed to process event {event.get('transactionHash', 'unknown')}: {e}")
                    continue
            
            if token_txs[token_address.lower()]:
                last_checked_tx[token_address.lower()] = token_txs[token_address.lower()][0]["signature"]
            elif found_last_tx:
                # If we found last_tx but no new transactions, keep the last_tx marker
                pass
        
        logger.info(f"Found {sum(len(txs) for txs in token_txs.values())} ERC20 token transactions")
        return token_txs
    except Exception as e:
        logger.error(f"Failed to get ERC20 token transactions: {str(e)}")
        return {}
