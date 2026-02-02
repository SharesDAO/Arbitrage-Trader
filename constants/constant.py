from enum import Enum


class PositionStatus(Enum):
    TRADABLE = 1
    PENDING_BUY = 2
    PENDING_SELL = 3
    PENDING_CANCEL = 4
    PENDING_LIQUIDATION = 5


class StrategyType(Enum):
    DCA = "DCA"
    GRID = "GRID"


class BlockchainType(Enum):
    CHIA = "CHIA"
    SOLANA = "SOLANA"
    EVM = "EVM"


REQUEST_TIMEOUT = 60
CONFIG = {}
CONFIG["MAX_ORDER_TIME_OFFSET"] = 600
CONFIG["RESERVE_RATIO"] = 0.1
# When you local system time is different from the server time, the offset between them. Don't change this unless you know what you are doing.

# EVM Chain Configuration
# RPC URLs can be Alchemy URLs (https://{chain}-mainnet.g.alchemy.com/v2/{API_KEY})
# or any other Ethereum RPC endpoint
# All chains share the same ALCHEMY_API_KEY environment variable
EVM_CHAINS = {
    "ethereum": {
        "chain_id": 1,
        "native_symbol": "ETH",
        "usdc_address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "usdc_decimals": 6,
        "rpc_env": "ETHEREUM_RPC_URL"
    },
    "base": {
        "chain_id": 8453,
        "native_symbol": "ETH",
        "usdc_address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "usdc_decimals": 6,
        "rpc_env": "BASE_RPC_URL"
    },
    "arbitrum": {
        "chain_id": 42161,
        "native_symbol": "ETH",
        "usdc_address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "usdc_decimals": 6,
        "rpc_env": "ARBITRUM_RPC_URL"
    },
    "bsc": {
        "chain_id": 56,
        "native_symbol": "BNB",
        "usdc_address": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        "usdc_decimals": 18,
        "rpc_env": "BSC_RPC_URL"
    }
}

# Alchemy API URLs for each chain
ALCHEMY_URLS = {
    "ethereum": "https://eth-mainnet.g.alchemy.com/v2",
    "base": "https://base-mainnet.g.alchemy.com/v2",
    "arbitrum": "https://arb-mainnet.g.alchemy.com/v2",
    "bsc": "https://bnb-mainnet.g.alchemy.com/v2"
}

# Estimated blocks per 24 hours based on average block time
# Average block times: Ethereum ~12s, Base ~2s, Arbitrum ~0.25s, BSC ~3s
BLOCKS_PER_24H = {
    "ethereum": 7200,   # 86400 / 12
    "base": 43200,      # 86400 / 2
    "arbitrum": 345600, # 86400 / 0.25
    "bsc": 28800        # 86400 / 3
}
