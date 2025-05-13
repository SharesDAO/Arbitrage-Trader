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

from util.bech32m import encode_puzzle_hash
from constants.constant import PositionStatus, CONFIG, REQUEST_TIMEOUT, StrategyType
from util.db import update_position, get_last_trade, delete_trade
from util.stock import STOCKS
from solana.rpc.api import Client
from solana.rpc.commitment import Commitment
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solana.rpc.types import TokenAccountOpts
from spl.token.instructions import get_associated_token_address
from solders.solders import Pubkey, transfer, Instruction, Message, Transaction, AccountMeta, Signature
from solders.system_program import TransferParams
from spl.memo.constants import MEMO_PROGRAM_ID
from spl.token.constants import TOKEN_PROGRAM_ID


coin_cache = TTLCache(maxsize=100, ttl=600)
price_cache = TTLCache(maxsize=100, ttl=30)
tx_cache = TTLCache(maxsize=100, ttl=30)
token_cache = TTLCache(maxsize=10, ttl=10)
last_checked_tx = {}
CHIA_PATH = "chia"
XCH_MOJO = 1000000000000
CAT_MOJO = 1000
SOLANA_DECIAML = 1000000000
SOLANA_URL = os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")


def send_asset(address: str, wallet_id: int, request: float, offer: float, logger, cid = "", order_type="LIMIT"):
    try:
        if CONFIG["BLOCKCHAIN"] == "SOLANA":
            return send_sol(address, {"customer_id": cid, "type": order_type, "offer": offer, "request": request}, logger)
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
                        # Get token amount from token program
                        if str(message.account_keys[
                                   instruction.program_id_index]) == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA":
                            # First 4 bytes are instruction type
                            parsed_data = base58.b58decode(instruction.data)
                            instruction_type = parsed_data[0]
                            # Check if it's a transfer instruction (type 2)
                            if instruction_type == 3 or instruction_type == 12:
                                tx["amount"] = struct.unpack("<Q", parsed_data[1:9])[0]
                if tx["memo"] and "customer_id" in tx["memo"]:
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
        for stock in CONFIG["SYMBOLS"]:
            token_mint = STOCKS[stock]["asset_id"].lower()
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
                        "signature": sig_info.signature,
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
                            if str(message.account_keys[instruction.program_id_index]) == "11111111111111111111111111111111" and len(instruction.data) >= 12:  # System Program ID
                                # First 4 bytes are instruction type
                                parsed_data = base58.b58decode(instruction.data)
                                instruction_type = struct.unpack("<I", parsed_data[0:4])[0]

                                # Check if it's a transfer instruction (type 2)
                                if instruction_type == 2:
                                    # Extract lamports from bytes 4-12
                                    tx["amount"] = struct.unpack("<Q", parsed_data[4:12])[0]
                            # Get token amount from token program
                            if str(message.account_keys[instruction.program_id_index]) == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA":
                                # First 4 bytes are instruction type
                                parsed_data = base58.b58decode(instruction.data)
                                instruction_type = parsed_data[0]
                                # Check if it's a transfer instruction (type 2)
                                if instruction_type == 3 or instruction_type == 12:
                                    tx["amount"] = struct.unpack("<Q", parsed_data[1:9])[0]
                    if tx["memo"] and "customer_id" in tx["memo"] > 0:
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
                amount = token_balance[STOCKS[trader.ticker]["asset_id"]]["balance"]
                if amount - expect_amount >= -0.003:
                    trader.position_status = PositionStatus.TRADABLE.name
                    trader.volume = amount
                    update_position(trader)
                    logger.info(f"Buy {trader.stock} confirmed")
                    confirmed = True
            if trader.type == StrategyType.GRID:
                asset_id = STOCKS[trader.ticker]["asset_id"].lower()
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
            asset_id = STOCKS[trader.ticker]["asset_id"].lower()
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
                        "balance": amount // 1_000_000_000,
                    }

                return token_balances
            return {}
        except Exception as e:
            print(f"Cannot get Solana token balance: {str(e)}")
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
        # Get the recipient's public key
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
        result = client.send_transaction(transaction).value
        logger.info(f"Sent {offer} SOL to {address} with memo: '{memo}', signature: {result}")
        if is_transaction_finalized(str(result), logger):
            return True
        else:
            logger.error(f"Transaction {result} is not finalized")
            return False
    except Exception as e:
        logger.error(f"Error sending transaction: {e}")
        raise


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
        result = client.send_transaction(transaction).value
        logger.info(f"Sent {offer} SOL to {address} with memo: '{memo}', signature: {result}")
        if is_transaction_finalized(str(result), logger):
            return True
        else:
            logger.error(f"Transaction {result} is not finalized")
            return False
    except Exception as e:
        logger.error(f"Error sending transaction: {e}")
        raise

MAX_RETRIES = 2

def is_transaction_finalized(signature: str, logger) -> bool:
    tx_sig = Signature.from_string(signature)

    # Method 1: Get signature status (faster, less detail)
    # Add retry for rpc call
    client = Client(SOLANA_URL)
    for attempt in range(MAX_RETRIES):
        try:
            status_response = client.get_signature_statuses([tx_sig], search_transaction_history=True)
            if status_response.value:
                status = status_response.value[0]
                if status is None or status.confirmation_status is None:
                    raise
                return True
            raise
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                logger.error(f"Request failed, retrying in 10s: {e}")
                time.sleep(10)
            else:
                logger.error(f"Failed to fetch signatures after {MAX_RETRIES} attempts")
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
    Get the current price of the cryptocurrency (XCH or SOL)
    """
    currency = CONFIG.get("CURRENCY", "XCH")
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
    except Exception as e:
        print(f"Cannot sign message {message} with DID {did}")
        raise e
