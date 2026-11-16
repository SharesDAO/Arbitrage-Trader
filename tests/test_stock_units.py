"""Regression tests without wallets, network requests, or trading SDKs."""
import importlib
import sys
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from constants.constant import CONFIG
from util import stock
from util.stock_units import token_per_share


class StockUnitsTest(unittest.TestCase):
    def setUp(self):
        self.old_config = dict(CONFIG)
        self.old_stocks = dict(stock.STOCKS)
        CONFIG.update(BLOCKCHAIN="SOLANA", SLIPPAGE=0, CRYPTO_MIN=100,
                      CRYPTO_MAX=100, MIN_PROFIT=0, SELL_ONLY_SYMBOLS=[], CURRENCY="SOL")
        stock.STOCKS["TQQQ"] = dict(asset_id="Mint", pool_id="pool",
                                   buy_addr="buy", sell_addr="sell")
        stock.cache.clear()

    def tearDown(self):
        CONFIG.clear()
        CONFIG.update(self.old_config)
        stock.STOCKS.clear()
        stock.STOCKS.update(self.old_stocks)
        stock.cache.clear()

    def version(self, **overrides):
        item = dict(chain="solana", address="Mint", model="legacy_fixed",
                    status="active", token_per_share="0.5")
        item.update(overrides)
        return item

    def quote(self, pool):
        response = MagicMock(status_code=200)
        response.json.return_value = pool
        with patch.object(stock.requests, "get", return_value=response):
            return stock.get_stock_price("TQQQ", MagicMock())

    def test_legacy_conversions(self):
        for ratio in ("0.5", "1", "2"):
            with self.subTest(ratio=ratio):
                stock.cache.clear()
                bid, ask = self.quote(dict(buy_price=100, sell_price=102,
                                          token_per_share=ratio))
                self.assertAlmostEqual(bid, 100 / float(ratio))
                self.assertAlmostEqual(ask, 102 / float(ratio))
        self.assertEqual(token_per_share({}, "Mint", "solana"), 1)

    def test_exact_ratio_and_slippage(self):
        CONFIG["SLIPPAGE"] = 0.01
        bid, ask = self.quote(dict(buy_price=100, sell_price=100,
                                  token_per_share=1, token_per_share_exact="0.5"))
        self.assertEqual((bid, ask), (198, 202))

    def test_address_version_overrides_pool_ratio(self):
        pool = dict(token_per_share=1, buy_price=100, sell_price=100,
                    stock_token_versions=[self.version(address="Other", token_per_share=4),
                                          self.version(status="legacy")])
        self.assertEqual(self.quote(pool), (200, 200))

    def test_scaled_ui_uses_projected_conversion(self):
        self.assertEqual(token_per_share(dict(stock_token_versions=[
            self.version(model="solana_scaled_ui", token_per_share="0.25")]),
            "Mint", "solana"), Decimal("0.25"))

    def test_version_errors_do_not_fallback_to_top_level(self):
        cases = [self.version(address="mint"), self.version(chain="arbitrum"),
                 self.version(status="staged"), self.version(model="unknown")]
        missing = self.version(model="solana_scaled_ui")
        del missing["token_per_share"]
        cases.append(missing)
        for item in cases:
            with self.subTest(item=item):
                pool = dict(token_per_share=1, buy_price=100, sell_price=100,
                            stock_token_versions=[item])
                self.assertEqual(self.quote(pool), (0, 0))
                stock.cache.clear()
        with self.assertRaises(ValueError):
            token_per_share(dict(stock_token_versions=[self.version(), self.version()]),
                            "Mint", "solana")

    def test_invalid_ratios_fail_closed(self):
        for ratio in (0, -1, 'NaN', 'Infinity', '1e999', '1e-999'):
            with self.subTest(ratio=ratio), self.assertRaises(ValueError):
                token_per_share(dict(token_per_share=ratio), "Mint", "solana")

    def test_evm_rebasing_and_address_case(self):
        CONFIG.update(BLOCKCHAIN="EVM", EVM_CHAIN="bsc")
        stock.STOCKS["TQQQ"]["asset_id"] = "0xAbC"
        pool = dict(buy_price=100, sell_price=100, token_per_share=0.5,
                    stock_token_versions=[self.version(chain="bnb", address="0xabc",
                                                       model="evm_rebasing")])
        self.assertEqual(self.quote(pool), (100, 100))

    def test_refresh_changes_conversion_with_price(self):
        self.quote(dict(buy_price=100, sell_price=100, token_per_share=1))
        stock.cache.clear()
        self.quote(dict(buy_price=100, sell_price=100, token_per_share=0.5))
        self.assertEqual(stock.get_token_per_share("TQQQ"), 0.5)

    def strategies(self):
        # Replace only external I/O dependencies; execute real strategy methods.
        crypto = MagicMock()
        db = MagicMock()
        fund = MagicMock()
        modules = {"util.crypto": crypto, "util.db": db, "util.sharesdao": fund}
        with patch.dict(sys.modules, modules):
            sys.modules.pop("stock_trader", None)
            sys.modules.pop("strategy.grid", None)
            sys.modules.pop("strategy.dca", None)
            grid = importlib.import_module("strategy.grid")
            dca = importlib.import_module("strategy.dca")
        grid.time = MagicMock()
        return grid, dca

    def trader(self, cls):
        trader = cls.__new__(cls)
        trader.ticker = trader.stock = "TQQQ"
        trader.logger = MagicMock()
        trader.max_price, trader.min_price = 100, 0
        trader.grid_width, trader.index = 0.1, 0
        trader.volume, trader.total_cost = 0.1, 0.05
        trader.buy_count = 0
        return trader

    def outgoing(self, module):
        # Main submits wallet orders; fund submits to its custody service.
        return module.send_asset if hasattr(module, "send_asset") else module.trade

    def test_grid_buy_and_sell_use_token_quantities(self):
        grid, _ = self.strategies()
        for ratio in (0.5, 1, 2):
            with self.subTest(ratio=ratio):
                self.quote(dict(buy_price=100, sell_price=100, token_per_share=ratio))
                trader = self.trader(grid.GridStockTrader)
                outgoing = self.outgoing(grid)
                outgoing.reset_mock()
                trader.sell_stock(100, 100 / ratio)
                args = outgoing.call_args.args
                self.assertAlmostEqual(args[3] if hasattr(grid, "send_asset") else args[2], 0.1 / ratio)
                self.assertAlmostEqual(args[4] if hasattr(grid, "send_asset") else args[3], 0.1)
                outgoing.reset_mock()
                trader.buy_stock(0.18, 100, 90 / ratio)
                args = outgoing.call_args.args
                self.assertAlmostEqual(args[3] if hasattr(grid, "send_asset") else args[2], 0.2 * ratio)
                self.assertAlmostEqual(trader.grid_step(), 0.1 / ratio)
                stock.cache.clear()

    def test_dca_buy_and_sell_use_converted_prices(self):
        _, dca = self.strategies()
        bid, _ = self.quote(dict(buy_price=100, sell_price=100, token_per_share=0.5))
        trader = self.trader(dca.DCAStockTrader)
        outgoing = self.outgoing(dca)
        outgoing.reset_mock()
        trader.sell_stock(100, bid)
        args = outgoing.call_args.args
        self.assertAlmostEqual(args[3] if hasattr(dca, "send_asset") else args[2], 0.2)
        trader.volume = trader.total_cost = 0
        outgoing.reset_mock()
        trader.buy_stock(0.2, 100, bid)
        args = outgoing.call_args.args
        self.assertAlmostEqual(args[3] if hasattr(dca, "send_asset") else args[2], 0.1)

    def test_reported_tqqq_order_with_half_ratio(self):
        grid, _ = self.strategies()
        self.quote(dict(buy_price="54.75002310469208415566065742",
                        sell_price=55, token_per_share=0.5))
        trader = self.trader(grid.GridStockTrader)
        trader.volume = 0.141447122
        CONFIG["CRYPTO_MIN"] = 67.7
        trader.max_price = (0.118815582 / trader.volume) * 67.7
        trader.sell_stock(67.7, trader.max_price / 0.5)
        args = self.outgoing(grid).call_args.args
        self.assertAlmostEqual(args[3] if hasattr(grid, "send_asset") else args[2],
                               0.237631164)


if __name__ == "__main__":
    unittest.main()
