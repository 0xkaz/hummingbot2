import asyncio
import logging
import time
from decimal import Decimal

import pandas as pd

from hummingbot.client.performance import PerformanceMetrics
from hummingbot.connector.gateway.amm_lp.gateway_evm_amm_lp import GatewayEVMAMMLP
from hummingbot.connector.gateway.amm_lp.gateway_in_flight_lp_order import GatewayInFlightLPOrder
from hummingbot.core.clock import Clock
from hummingbot.core.event.events import TradeType
from hummingbot.core.gateway.gateway_http_client import GatewayHttpClient
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.logger import HummingbotLogger
from hummingbot.strategy.market_trading_pair_tuple import MarketTradingPairTuple
from hummingbot.strategy.strategy_py_base import StrategyPyBase

ulp_logger = None
s_decimal_0 = Decimal("0")


def get_position_status_by_price_range(price: Decimal, lower_price: Decimal, upper_price: Decimal,
                                       buffer_spread: Decimal = Decimal("0")):
    """
    This returns the status of a position based on last price and buffer spread.
    There are 3 possible statuses: In range, In buffered range, Out of range.
    """
    if lower_price <= price <= upper_price:
        return "[In range]"
    elif is_price_in_range(price, lower_price, upper_price, buffer_spread):
        return "[In buffered range]"
    else:
        return "[Out of range]"


def is_price_in_range(price: Decimal, lower_price: Decimal, upper_price: Decimal,
                      buffer_spread: Decimal = Decimal("0")):
    """
    Check if price is in range of lower and upper price with buffer spread.
    """
    if ((lower_price * (Decimal("1") - buffer_spread)) <= price <=
            (upper_price * (Decimal("1") + buffer_spread))):
        return True
    else:
        return False


class AmmV3LpStrategy(StrategyPyBase):

    @classmethod
    def logger(cls) -> HummingbotLogger:
        global ulp_logger
        if ulp_logger is None:
            ulp_logger = logging.getLogger(__name__)
        return ulp_logger

    def __init__(self,
                 market_info: MarketTradingPairTuple,
                 fee_tier: str,
                 price_spread: Decimal,
                 buffer_spread: Decimal,
                 min_amount: Decimal,
                 max_amount: Decimal,
                 min_profitability: Decimal,
                 status_report_interval: int = 10):
        super().__init__()
        self._market_info = market_info
        self._fee_tier = fee_tier
        self._price_spread = price_spread
        self._min_amount = min_amount
        self._max_amount = max_amount
        self._buffer_spread = buffer_spread
        self._min_profitability = min_profitability

        self._ev_loop = asyncio.get_event_loop()
        self._last_timestamp = 0
        self._status_report_interval = status_report_interval
        self.add_markets([market_info.market])
        self._connector_ready = False
        self._last_price = s_decimal_0
        self._main_task = None
        self._fetch_prices_task = None

        self._connector: GatewayEVMAMMLP = self._market_info.market
        self._connector.set_update_balance_interval(status_report_interval)
        self._connector.set_pool_interval(status_report_interval)

    @property
    def connector_name(self):
        return self._market_info.market.display_name

    @property
    def base_asset(self):
        return self._market_info.base_asset

    @property
    def quote_asset(self):
        return self._market_info.quote_asset

    @property
    def trading_pair(self):
        return self._market_info.trading_pair

    @property
    def active_positions(self):
        return [pos for pos in self._market_info.market.amm_lp_orders if
                pos.is_nft and pos.trading_pair == self.trading_pair]

    @property
    def active_orders(self):
        return [pos for pos in self._market_info.market.amm_lp_orders if
                not pos.is_nft and pos.trading_pair == self.trading_pair]

    async def get_pool_price(self, update_volatility: bool = False) -> float:
        prices = await self._market_info.market.get_price(self.trading_pair, self._fee_tier)
        if prices:
            return Decimal(prices[-1])
        else:
            return s_decimal_0

    def active_positions_df(self) -> pd.DataFrame:
        columns = ["Id", "Fee Tier", "Symbol", "Price Range", "Base/Quote Amount", "Unclaimed Base/Quote Fees", ""]
        data = []
        if len(self.active_positions) > 0:
            for position in self.active_positions:
                data.append([
                    position.token_id,
                    position.fee_tier,
                    position.trading_pair,
                    f"{PerformanceMetrics.smart_round(position.adjusted_lower_price, 8)} - "
                    f"{PerformanceMetrics.smart_round(position.adjusted_upper_price, 8)}",
                    f"{PerformanceMetrics.smart_round(position.amount_0, 8)} / "
                    f"{PerformanceMetrics.smart_round(position.amount_1, 8)}",
                    f"{PerformanceMetrics.smart_round(position.unclaimed_fee_0, 8)} / "
                    f"{PerformanceMetrics.smart_round(position.unclaimed_fee_1, 8)}",
                    f"{get_position_status_by_price_range(self._last_price, position.adjusted_lower_price, position.adjusted_upper_price, self._buffer_spread)}",
                ])
        return pd.DataFrame(data=data, columns=columns)

    async def format_status(self) -> str:
        """
        Returns a status string formatted to display nicely on terminal. The strings composes of 4 parts: market,
        assets, spread and warnings(if any).
        """
        if not self._connector_ready:
            return f"{self.connector_name} connector not ready."

        columns = ["Exchange", "Market", "Pool Price"]
        data = []
        market, trading_pair, base_asset, quote_asset = self._market_info
        data.append([
            market.display_name,
            trading_pair,
            PerformanceMetrics.smart_round(Decimal(str(self._last_price)), 8)
        ])
        markets_df = pd.DataFrame(data=data, columns=columns)
        lines = []
        lines.extend(["", "  Markets:"] + ["    " + line for line in markets_df.to_string(index=False).split("\n")])

        # See if there're any active positions.
        if len(self.active_positions) > 0:
            pos_info_df = self.active_positions_df()
            lines.extend(
                ["", "  Positions:"] + ["    " + line for line in pos_info_df.to_string(index=False).split("\n")])
        else:
            lines.extend(["", "  No active positions."])

        assets_df = self.wallet_balance_data_frame([self._market_info])
        lines.extend(["", "  Assets:"] +
                     ["    " + line for line in str(assets_df).split("\n")])

        warning_lines = self.network_warning([self._market_info])
        warning_lines.extend(self.balance_warning([self._market_info]))
        if len(warning_lines) > 0:
            lines.extend(["", "*** WARNINGS ***"] + warning_lines)

        return "\n".join(lines)

    def tick(self, timestamp: float):
        """
        Clock tick entry point, is run every second (on normal tick setting).
        :param timestamp: current tick timestamp
        """
        if not self._connector_ready:
            self._connector_ready = self._market_info.market.ready
            if not self._connector_ready:
                self.logger().warning(f"{self.connector_name} connector is not ready. Please wait...")
                return
            else:
                self.logger().info(f"{self.connector_name} connector is ready. Trading started.")

        if time.time() - self._last_timestamp > self._status_report_interval:
            if self._main_task is None or self._main_task.done():
                self._main_task = safe_ensure_future(self.main())
            self._last_timestamp = time.time()

    async def main(self):
        if len(self.active_orders) == 0:  # this ensures that there'll always be one lp order per time
            position_closed = await self.close_matured_positions()

            if position_closed:
                await self.reset_balance()

                lower_price, upper_price = await self.propose_position_boundary()
                if lower_price + upper_price != s_decimal_0:
                    await self.execute_proposal(lower_price, upper_price)

    def any_active_position(self, current_price: Decimal, buffer_spread: Decimal = Decimal("0")) -> bool:
        """
        We use this to know if any existing position is in-range.
        :return: True/False
        """
        for position in self.active_positions:
            if is_price_in_range(current_price, position.lower_price, position.upper_price, buffer_spread):
                return True
        return False

    async def propose_position_boundary(self):
        """
        We use this to create proposal for new range positions
        :return : lower_price, upper_price
        """
        lower_price = s_decimal_0
        upper_price = s_decimal_0
        current_price = await self.get_pool_price()

        if current_price != s_decimal_0:
            self._last_price = current_price
            if not self.any_active_position(Decimal(current_price),
                                            self._buffer_spread):  # only set prices if there's no active position
                half_spread = self._price_spread / Decimal("2")
                lower_price = (current_price * (Decimal("1") - half_spread))
                upper_price = (current_price * (Decimal("1") + half_spread))
        lower_price = max(s_decimal_0, lower_price)
        return lower_price, upper_price

    async def execute_proposal(self, lower_price: Decimal, upper_price: Decimal):
        """
        This execute proposal generated earlier by propose_position_boundary function.
        :param lower_price: lower price for position to be created
        :param upper_price: upper price for position to be created
        """
        await self._market_info.market._update_balances()  # this is to ensure that we have the latest balances

        base_balance = self._market_info.market.get_available_balance(self.base_asset)
        quote_balance = self._market_info.market.get_available_balance(self.quote_asset)

        proposed_lower_price = lower_price
        proposed_upper_price = upper_price
        current_price = Decimal(await self.get_pool_price())

        # Make sure we don't create position with too little amount of base asset
        if base_balance < self._min_amount:
            base_balance = s_decimal_0
            # Add 0.1% gap from current price to make sure we don't use any base balance in new position
            # NOTE: Please make sure buffer_spread is bigger than 0.1% to avoid the position being closed immediately
            proposed_upper_price = current_price * (Decimal("1") - Decimal("0.1") / 100)
            # We use the whole price spread for lower bound
            proposed_lower_price = current_price * (Decimal("1") - self._price_spread)

        # Make sure we don't create position with too little amount of quote asset
        if (quote_balance / self._last_price) < self._min_amount:
            quote_balance = s_decimal_0
            # We use the whole price spread for upper bound
            proposed_upper_price = current_price * (Decimal("1") + self._price_spread)
            # Add 0.1% gap from current price to make sure we don't use any quote balance in new position
            # NOTE: Please make sure buffer_spread is bigger than 0.1% to avoid the position being closed immediately
            proposed_lower_price = current_price * (Decimal("1") + Decimal("0.1") / 100)

        if base_balance + quote_balance == s_decimal_0:
            self.log_with_clock(logging.INFO,
                                "Both balances exhausted. Add more assets.")
        else:
            self.log_with_clock(logging.INFO, f"Creating new position over {lower_price} to {upper_price} price range.")
            self.log_with_clock(logging.INFO, f"Base balance: {base_balance}, quote balance: {quote_balance}")
            self._market_info.market.add_liquidity(self.trading_pair,
                                                   min(base_balance, self._max_amount),
                                                   min(quote_balance, (self._max_amount * self._last_price)),
                                                   proposed_lower_price,
                                                   proposed_upper_price,
                                                   self._fee_tier)

    async def close_matured_positions(self) -> bool:
        """
        This closes out-of-range positions that have more than the min profitability.
        """
        closed_position = False
        for position in self.active_positions:
            if not is_price_in_range(self._last_price, position.lower_price, position.upper_price,
                                     Decimal("0")):  # out-of-range
                if position.unclaimed_fee_0 + (
                        position.unclaimed_fee_1 / self._last_price) > self._min_profitability:  # matured
                    self.log_with_clock(logging.INFO,
                                        f"Closing position with Id {position.token_id} (Matured position)."
                                        f"Unclaimed base fee: {position.unclaimed_fee_0}, unclaimed quote fee: {position.unclaimed_fee_1}")
                    await self._market_info.market.remove_liquidity(self.trading_pair, position.token_id)
                    closed_position = True
                else:
                    closed_position = await self.close_out_of_buffered_range_position(position)

        if len(self.active_positions) == 0:
            self.log_with_clock(logging.INFO,
                                "Closing matured positions completed. No active positions left.")
            closed_position = True

        return closed_position

    async def close_out_of_buffered_range_position(self, position: GatewayInFlightLPOrder) -> bool:
        """
        This closes out-of-range positions that are too far from last price.
        """
        if not is_price_in_range(self._last_price, position.lower_price, position.upper_price, self._buffer_spread):  # out-of-range
            self.log_with_clock(logging.INFO,
                                f"Closing position with Id {position.token_id} (Out of range)."
                                f"Unclaimed base fee: {position.unclaimed_fee_0}, unclaimed quote fee: {position.unclaimed_fee_1}")
            await self._market_info.market.remove_liquidity(self.trading_pair, position.token_id)
            return True

        return False

    async def reset_balance(self):
        """
        This resets the balance of the wallet to create new lp position.
        """
        await self._market_info.market._update_balances()
        base_balance = self._market_info.market.get_available_balance(self.base_asset)
        quote_balance = self._market_info.market.get_available_balance(self.quote_asset)
        current_price = Decimal(await self.get_pool_price())

        if (base_balance > self._min_amount) and ((quote_balance / current_price) > self._min_amount):
            self.log_with_clock(logging.INFO,
                                "Both balances are above minimum amount. No need to reset balance.")
            return

        base, quote = list(self.trading_pair)[0].split("-")
        chain = self._connector.chain
        network = self._connector.network
        connector = "uniswap"
        total_balance_in_base: Decimal = base_balance + (quote_balance / current_price)
        base_balance_needed: Decimal = total_balance_in_base / Decimal("2")
        balance_diff_in_base: Decimal = base_balance_needed - base_balance

        if (base_balance_needed < self._min_amount):
            self.log_with_clock(logging.INFO,
                                "Total balance is below minimum amount. No need to reset balance.")
            return

        slippage_buffer = Decimal("0.01")

        if balance_diff_in_base > s_decimal_0:
            self.log_with_clock(logging.INFO,
                                f"Resetting balance. Adding {abs(balance_diff_in_base)} {base} to wallet.")
            trade_side = TradeType.BUY
        else:
            self.log_with_clock(logging.INFO,
                                f"Resetting balance. Adding {abs(balance_diff_in_base)} {quote} to wallet.")
            trade_side = TradeType.SELL

        # add slippage buffer to current price
        if trade_side == TradeType.BUY:
            price = current_price * (Decimal("1") + slippage_buffer)
        else:
            price = current_price * (Decimal("1") - slippage_buffer)
        self.logger().info(f"Swap Limit Price: {price}")

        # execute swap
        self.logger().info(f"POST /amm/trade [ connector: {connector}, base: {base}, quote: {quote}, amount: {abs(balance_diff_in_base)}, side: {trade_side}, price: {price} ]")
        trade_date = await GatewayHttpClient.get_instance().amm_trade(
            chain,
            network,
            connector,
            self._connector.address,
            base,
            quote,
            trade_side,
            abs(balance_diff_in_base),
            price
        )

        await self.poll_transaction(chain, network, trade_date['txHash'])
        await self.get_balance(chain, network, self._connector.address, base, quote)

    # fetch and print balance of base and quote tokens
    async def get_balance(self, chain, network, address, base, quote):
        self.logger().info(f"POST /network/balance [ address: {address}, base: {base}, quote: {quote} ]")
        balance_data = await GatewayHttpClient.get_instance().get_balances(
            chain,
            network,
            address,
            [base, quote]
        )
        self.logger().info(f"Balances for {address}: {balance_data['balances']}")

    # continuously poll for transaction until confirmed
    async def poll_transaction(self, chain, network, tx_hash):
        pending: bool = True
        while pending is True:
            self.logger().info(f"POST /network/poll [ txHash: {tx_hash} ]")
            poll_data = await GatewayHttpClient.get_instance().get_transaction_status(
                chain,
                network,
                tx_hash
            )
            transaction_status = poll_data.get("txStatus")
            if transaction_status == 1:
                self.logger().info(f"Trade with transaction hash {tx_hash} has been executed successfully.")
                pending = False
            elif transaction_status in [-1, 0, 2]:
                self.logger().info(f"Trade is pending confirmation, Transaction hash: {tx_hash}")
                await asyncio.sleep(2)
            else:
                self.logger().info(f"Unknown txStatus: {transaction_status}")
                self.logger().info(f"{poll_data}")
                pending = False

    def stop(self, clock: Clock):
        if self._main_task is not None:
            self._main_task.cancel()
            self._main_task = None
