import calendar
import datetime
import json
import os
import re
import subprocess
import time
from datetime import datetime
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
SOLANA_URL = os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

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


def get_sol_txs():
    try:
        client = Client(SOLANA_URL)
        
        # Get recent confirmed signatures for transactions involving this wallet
        response = client.get_signatures_for_address(
            Pubkey.from_string(CONFIG['ADDRESS']),
            limit=100,
            commitment=Commitment("confirmed")
        )
        
        if not response.value:
            return []
            
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
            
            # Create a transaction object with similar structure to Chia transactions
            tx = {
                "signature": sig_info.signature,
                "sent": 0,  # Assume it's a received transaction
                "amount": 0,
                "memo": {"customer_id": "", "symbol": ""},
                "timestamp": sig_info.block_time if sig_info.block_time else 0,
                "slot": sig_info.slot
            }
            
            # Extract transaction amount and memo if available
            if tx_data.meta and tx_data.meta.pre_balances and tx_data.meta.post_balances:
                # Calculate the balance change for the account
                account_index = None
                for i, key in enumerate(tx_data.transaction.message.account_keys):
                    if key.to_string() == CONFIG['ADDRESS']:
                        account_index = i
                        break
                
                if account_index is not None:
                    pre_balance = tx_data.meta.pre_balances[account_index]
                    post_balance = tx_data.meta.post_balances[account_index]
                    tx["amount"] = (post_balance - pre_balance) / 1_000_000_000  # Convert lamports to SOL
            
            # Extract memo if available
            if tx_data.transaction.message.instructions:
                for ix in tx_data.transaction.message.instructions:
                    # Check if instruction is for Memo Program
                    if isinstance(ix.program_id, Pubkey) and ix.program_id.to_string() == "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr":
                        # Decode the memo data
                        try:
                            memo_data = bytes(ix.data).decode('utf-8')
                            # Try to parse as JSON
                            try:
                                tx["memo"] = json.loads(memo_data)
                            except:
                                tx["memo"] = {"data": memo_data}
                        except:
                            pass
            
            transactions.append(tx)
        
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


def get_spl_token_txs():
    try:
        client = Client(SOLANA_URL)
        token_balance = get_token_balance()
        token_txs = {}
        
        # For each token in the balance, get its transactions
        for token_mint, token_info in token_balance.items():
            # Get token accounts for this mint and owner
            token_accounts_response = client.get_token_accounts_by_owner(
                Pubkey.from_string(CONFIG['ADDRESS']),
                TokenAccountOpts(mint=Pubkey.from_string(token_mint))
            )
            
            if not token_accounts_response.value:
                continue
                
            token_txs[token_mint.lower()] = []
            
            # For each token account, get its transaction history
            for token_account in token_accounts_response.value:
                account_pubkey = token_account.pubkey
                
                # Get transaction signatures for this token account
                sigs_response = client.get_signatures_for_address(
                    account_pubkey,
                    limit=50,
                    commitment=Commitment("confirmed")
                )
                
                if not sigs_response.value:
                    continue
                    
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
                    tx = {
                        "signature": sig_info.signature,
                        "sent": 0,  # Assuming it's received
                        "asset_id": token_mint,
                        "amount": 0,
                        "memo": {"customer_id": "", "symbol": ""},
                        "timestamp": sig_info.block_time if sig_info.block_time else 0,
                        "slot": sig_info.slot
                    }
                    
                    # Find token transfer amount by analyzing the transaction
                    if tx_data.meta and tx_data.meta.post_token_balances and tx_data.meta.pre_token_balances:
                        # Look for the relevant token account's balance change
                        pre_balance = None
                        post_balance = None
                        
                        for token_balance in tx_data.meta.pre_token_balances:
                            if token_balance.owner == CONFIG['ADDRESS'] and token_balance.mint == token_mint:
                                pre_balance = int(token_balance.ui_token_amount.amount) if token_balance.ui_token_amount.amount else 0
                                break
                                
                        for token_balance in tx_data.meta.post_token_balances:
                            if token_balance.owner == CONFIG['ADDRESS'] and token_balance.mint == token_mint:
                                post_balance = int(token_balance.ui_token_amount.amount) if token_balance.ui_token_amount.amount else 0
                                break
                        
                        if pre_balance is not None and post_balance is not None:
                            tx["amount"] = (post_balance - pre_balance)  # Already in token units (like CAT_MOJO)
                    
                    # Extract memo if available (same logic as in get_sol_txs)
                    if tx_data.transaction.message.instructions:
                        for ix in tx_data.transaction.message.instructions:
                            # Check if instruction is for Memo Program
                            if isinstance(ix.program_id, Pubkey) and ix.program_id.to_string() == "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr":
                                try:
                                    memo_data = bytes(ix.data).decode('utf-8')
                                    # Try to parse as JSON
                                    try:
                                        tx["memo"] = json.loads(memo_data)
                                    except:
                                        tx["memo"] = {"data": memo_data}
                                except:
                                    pass
                    
                    # Only add transactions with actual token transfers
                    if tx["amount"] != 0:
                        token_txs[token_mint.lower()].append(tx)
        
        return token_txs
    except Exception as e:
        print(f"Failed to get SPL token transactions: {str(e)}")
        return {}


def check_pending_positions(traders, logger):
    token_balance = get_token_balance()
    
    # Get transactions based on blockchain type
    if CONFIG["BLOCKCHAIN"] == "SOLANA":
        crypto_txs = get_sol_txs()
        all_token_txs = get_spl_token_txs()
        logger.info(f"Fetched {len(crypto_txs)} SOL txs.")
        SOL_LAMPORTS = 1_000_000_000  # 10^9 lamports in 1 SOL
        token_divisor = 1  # SPL tokens amounts are already adjusted in get_spl_token_txs
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
    if CONFIG["BLOCKCHAIN"] == "SOLANA":
        # Use Solana RPC API to get SPL token balances
        try:
            # Get Solana client based on network configuration
            client = Client(SOLANA_URL)
            
            # Get all SPL token accounts owned by this wallet address
            response = client.get_token_accounts_by_owner(
                Pubkey.from_string(CONFIG["ADDRESS"]),
                TokenAccountOpts(program_id=Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")),
            )
            
            if response.value is not None:
                token_balances = {}
                
                for account in response.value:
                    # Parse token data from the response
                    account_data = account.account.data.parsed["info"]
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
        return private_key.sign_message(message.encode("utf-8")).to_bytes().hex()
    if CONFIG["BLOCKCHAIN"] == "CHIA":
        private_key = PrivateKey.from_bytes(bytes.fromhex(os.environ["DID_PRIVATE_KEY"]))
        synthetic_secret_key = calculate_synthetic_secret_key(private_key, DEFAULT_HIDDEN_PUZZLE_HASH)
        hex_message = Program.to((CHIP_0002_SIGN_MESSAGE_PREFIX, message)).get_tree_hash()
        return str(AugSchemeMPL.sign(synthetic_secret_key, hex_message))
