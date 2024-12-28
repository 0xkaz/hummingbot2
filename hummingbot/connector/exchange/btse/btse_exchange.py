import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from bidict import bidict

from hummingbot.connector.constants import s_decimal_NaN
from hummingbot.connector.exchange.btse import (
    btse_constants as CONSTANTS,
    btse_utils,
    btse_web_utils as web_utils,
)
from hummingbot.connector.exchange.btse.btse_api_order_book_data_source import BtseAPIOrderBookDataSource
from hummingbot.connector.exchange.btse.btse_api_user_stream_data_source import BtseAPIUserStreamDataSource
from hummingbot.connector.exchange.btse.btse_auth import BtseAuth
from hummingbot.connector.exchange_py_base import ExchangePyBase
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.connector.utils import TradeFillOrderDetails, combine_to_hb_trading_pair
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderUpdate, TradeUpdate
from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource
from hummingbot.core.data_type.trade_fee import DeductedFromReturnsTradeFee, TokenAmount, TradeFeeBase
from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource
from hummingbot.core.event.events import MarketEvent, OrderFilledEvent
from hummingbot.core.utils.async_utils import safe_gather
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory

if TYPE_CHECKING:
    from hummingbot.client.config.config_helpers import ClientConfigAdapter


class BtseExchange(ExchangePyBase):
    UPDATE_ORDER_STATUS_MIN_INTERVAL = 10.0

    web_utils = web_utils

    def __init__(self,
                 client_config_map: "ClientConfigAdapter",
                 btse_api_key: str,
                 btse_api_secret: str,
                 trading_pairs: Optional[List[str]] = None,
                 trading_required: bool = True,
                 domain: str = CONSTANTS.DEFAULT_DOMAIN,
                 ):
        self.api_key = btse_api_key
        self.secret_key = btse_api_secret
        self._domain = domain
        self._trading_required = trading_required
        self._trading_pairs = trading_pairs
        self._last_trades_poll_btse_timestamp = 1569888.0
        super().__init__(client_config_map)

    @staticmethod
    def btse_order_type(order_type: OrderType) -> str:
        return order_type.name.upper()

    @staticmethod
    def to_hb_order_type(btse_type: str) -> OrderType:
        return OrderType[btse_type]

    @property
    def authenticator(self):
        return BtseAuth(
            api_key=self.api_key,
            secret_key=self.secret_key,
            time_provider=self._time_synchronizer)

    @property
    def name(self) -> str:
        if self._domain == "btse_main":
            return "btse"
        else:
            return f"{self._domain}"

    @property
    def rate_limits_rules(self):
        return CONSTANTS.RATE_LIMITS

    @property
    def domain(self):
        return self._domain

    @property
    def client_order_id_max_length(self):
        return CONSTANTS.MAX_ORDER_ID_LEN

    @property
    def client_order_id_prefix(self):
        return CONSTANTS.HBOT_ORDER_ID_PREFIX

    @property
    def trading_rules_request_path(self):
        return CONSTANTS.EXCHANGE_INFO_PATH_URL

    @property
    def trading_pairs_request_path(self):
        return CONSTANTS.EXCHANGE_INFO_PATH_URL

    @property
    def check_network_request_path(self):
        return CONSTANTS.PING_PATH_URL

    @property
    def trading_pairs(self):
        return self._trading_pairs

    @property
    def is_cancel_request_in_exchange_synchronous(self) -> bool:
        return True

    @property
    def is_trading_required(self) -> bool:
        return self._trading_required

    def supported_order_types(self):
        return [OrderType.LIMIT, OrderType.MARKET]

    async def get_all_pairs_prices(self) -> List[Dict[str, str]]:
        pairs_prices = await self._api_get(path_url=CONSTANTS.TICKER_PRICE_CHANGE_PATH_URL)        
        return pairs_prices

    def _is_request_exception_related_to_time_synchronizer(self, request_exception: Exception):
        error_description = str(request_exception)
        is_time_synchronizer_related = ("-1021" in error_description
                                        and "Timestamp for this request" in error_description)
        return is_time_synchronizer_related

    def _is_order_not_found_during_status_update_error(self, status_update_exception: Exception) -> bool:
        return str(CONSTANTS.ORDER_NOT_EXIST_ERROR_CODE) in str(
            status_update_exception
        ) and CONSTANTS.ORDER_NOT_EXIST_MESSAGE in str(status_update_exception)

    def _is_order_not_found_during_cancelation_error(self, cancelation_exception: Exception) -> bool:
        return str(CONSTANTS.UNKNOWN_ORDER_ERROR_CODE) in str(
            cancelation_exception
        ) and CONSTANTS.UNKNOWN_ORDER_MESSAGE in str(cancelation_exception)

    def _create_web_assistants_factory(self) -> WebAssistantsFactory:
        return web_utils.build_api_factory(
            throttler=self._throttler,
            time_synchronizer=self._time_synchronizer,
            domain=self._domain,
            auth=self._auth)

    def _create_order_book_data_source(self) -> OrderBookTrackerDataSource:
        return BtseAPIOrderBookDataSource(
            trading_pairs=self._trading_pairs,
            connector=self,
            domain=self.domain,
            api_factory=self._web_assistants_factory)

    def _create_user_stream_data_source(self) -> UserStreamTrackerDataSource:        
        return BtseAPIUserStreamDataSource(
            auth=self._auth,
            time_synchronizer=self._time_synchronizer,
            throttler=self._throttler,            
            api_factory=self._web_assistants_factory,
            domain=self.domain,
        )

    async def _place_order(self,
                           order_id: str,
                           trading_pair: str,
                           amount: Decimal,
                           trade_type: TradeType,
                           order_type: OrderType,
                           price: Decimal,
                           **kwargs) -> Tuple[str, float]:
        order_result = None
        amount_str = f"{round(amount, 8):f}"
        price_str = f"{round(price, 8):f}"                
        type_str = BtseExchange.btse_order_type(order_type)
        side_str = CONSTANTS.SIDE_BUY if trade_type is TradeType.BUY else CONSTANTS.SIDE_SELL
        symbol = await self.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
        api_params = {"symbol": symbol,
                      "side": side_str,
                      "size": amount_str,
                      "type": type_str,                      
                      }        
        if order_type == OrderType.LIMIT:
            api_params['price'] = price_str        
        try:
            order_result = await self._api_post(
                path_url=CONSTANTS.ORDER_PATH_URL,
                data=api_params,
                is_auth_required=True)            
            o_id = str(order_result[0]["orderID"])
            transact_time = order_result[0]["timestamp"] * 1e-3
        except IOError as e:
            error_description = str(e)
            is_server_overloaded = ("status is 503" in error_description
                                    and "Unknown error, please check your request or try again later." in error_description)
            if is_server_overloaded:
                o_id = "UNKNOWN"
                transact_time = self._time_synchronizer.time()
            else:
                raise
        return o_id, transact_time


    def _get_fee(self,
                 base_currency: str,
                 quote_currency: str,
                 order_type: OrderType,
                 order_side: TradeType,
                 amount: Decimal,
                 price: Decimal = s_decimal_NaN,
                 is_maker: Optional[bool] = None) -> TradeFeeBase:
        is_maker = order_type is OrderType.LIMIT_MAKER
        return DeductedFromReturnsTradeFee(percent=self.estimate_fee_pct(is_maker))
    async def _place_cancel(self, order_id: str, tracked_order: InFlightOrder):
        symbol = await self.exchange_symbol_associated_to_pair(trading_pair=tracked_order.trading_pair)
        api_params = {
            "symbol": symbol,
            "orderId": order_id,
        }        
        cancel_result = await self._api_delete(
            path_url=CONSTANTS.ORDER_PATH_URL,
            params=api_params,
            is_auth_required=True)                
        if len(cancel_result) == 0 or cancel_result[0].get("status") == CONSTANTS.ORDER_STATE["ORDER_CANCELLED"]:
            return True
        return False    

    async def _format_trading_rules(self, exchange_info_dict: Dict[str, Any]) -> List[TradingRule]:
        """
        Example:
            [
                {
                    "symbol": "1INCH-AED",
                    "last": 0.0,
                    "lowestAsk": 0.0,
                    "highestBid": 0.0,
                    "percentageChange": 0.0,
                    "volume": 0.0,
                    "high24Hr": 0.0,
                    "low24Hr": 0.0,
                    "base": "1INCH",
                    "quote": "AED",
                    "active": true,
                    "size": 0.0,
                    "minValidPrice": 1.0E-4,
                    "minPriceIncrement": 1.0E-4,
                    "minOrderSize": 0.1,
                    "maxOrderSize": 3000000.0,
                    "minSizeIncrement": 0.1,
                    "openInterest": 0.0,
                    "openInterestUSD": 0.0,
                    "contractStart": 0,
                    "contractEnd": 0,
                    "timeBasedContract": false,
                    "openTime": 0,
                    "closeTime": 0,
                    "startMatching": 0,
                    "inactiveTime": 0,
                    "fundingRate": 0.0,
                    "contractSize": 0.0,
                    "maxPosition": 0,
                    "minRiskLimit": 0,
                    "maxRiskLimit": 0,
                    "availableSettlement": null,
                    "futures": false,
                    "isMarketOpenToOtc": true,
                    "isMarketOpenToSpot": false
                },
                {
                    "symbol": "1INCH-AUD",
                    "last": 0.0,
                    "lowestAsk": 0.0,
                    "highestBid": 0.0,
                    "percentageChange": 0.0,
                    "volume": 0.0,
                    "high24Hr": 0.0,
                    "low24Hr": 0.0,
                    "base": "1INCH",
                    "quote": "AUD",
                    "active": true,
                    "size": 0.0,
                    "minValidPrice": 1.0E-4,
                    "minPriceIncrement": 1.0E-4,
                    "minOrderSize": 0.1,
                    "maxOrderSize": 3000000.0,
                    "minSizeIncrement": 0.1,
                    "openInterest": 0.0,
                    "openInterestUSD": 0.0,
                    "contractStart": 0,
                    "contractEnd": 0,
                    "timeBasedContract": false,
                    "openTime": 0,
                    "closeTime": 0,
                    "startMatching": 0,
                    "inactiveTime": 0,
                    "fundingRate": 0.0,
                    "contractSize": 0.0,
                    "maxPosition": 0,
                    "minRiskLimit": 0,
                    "maxRiskLimit": 0,
                    "availableSettlement": null,
                    "futures": false,
                    "isMarketOpenToOtc": true,
                    "isMarketOpenToSpot": false
                },
            ]
        """
        trading_pair_rules = exchange_info_dict        
        retval = []
        for rule in trading_pair_rules:
            if "_" not in rule.get("symbol"):
                try:                
                    trading_pair = await self.trading_pair_associated_to_exchange_symbol(rule.get("symbol"))

                    min_order_size = rule.get("minOrderSize")
                    min_price_increment = rule.get("minPriceIncrement")
                    min_base_amount_increment = rule.get("minSizeIncrement")
                    min_notional_size = rule.get("minValidPrice")

                    retval.append(
                        TradingRule(trading_pair,
                                    min_order_size=min_order_size,
                                    min_price_increment=Decimal(min_price_increment),
                                    min_base_amount_increment=Decimal(min_base_amount_increment),
                                    min_notional_size=Decimal(min_notional_size)))

                except Exception:
                    self.logger().exception(f"Error parsing the trading pair rule {rule}. Skipping.")
        return retval

    async def _update_trading_fees(self):
        """
        Update fees information from the exchange
        """
        pass

    async def _status_polling_loop_fetch_updates(self):
        await self._update_order_fills_from_trades()
        await super()._status_polling_loop_fetch_updates()

    async def _user_stream_event_listener(self):
        """
        This functions runs in background continuously processing the events received from the exchange by the user
        stream data source. It keeps reading events from the queue until the task is interrupted.
        The events received are balance updates, order updates and trade events.
        """
        async for event_message in self._iter_user_event_queue():
            try:
                event_type = event_message.get("topic")
                event_message_data = event_message.get("data")[0]
                # Refer to https://github.com/btse-exchange/btse-official-api-docs/blob/master/user-data-stream.md
                # As per the order update section in Btse the ID of the order being canceled is under the "C" key
                if event_type == "fills":
                    execution_type = event_message.get("topic")
                    if execution_type != "CANCELED":
                        client_order_id = event_message_data.get("clOrderId")
                    else:
                        client_order_id = event_message_data.get("clOrderId")

                    if execution_type == "TRADE":
                        tracked_order = self._order_tracker.all_fillable_orders.get(client_order_id)
                        if tracked_order is not None:
                            fee = TradeFeeBase.new_spot_fee(
                                fee_schema=self.trade_fee_schema(),
                                trade_type=tracked_order.trade_type,
                                percent_token=event_message_data["feeCurrency"],
                                flat_fees=[TokenAmount(amount=Decimal(event_message_data["feeAmount"]), token=event_message_data["feeCurrency"])]
                            )
                            trade_update = TradeUpdate(
                                trade_id=str(event_message_data["tradeId"]),
                                client_order_id=client_order_id,
                                exchange_order_id=str(event_message_data["orderId"]),
                                trading_pair=tracked_order.trading_pair,
                                fee=fee,
                                fill_base_amount=Decimal(event_message_data["base"]),
                                fill_quote_amount=Decimal(event_message_data["base"]) * Decimal(event_message_data["quote"]),
                                fill_price=Decimal(event_message_data["quote"]),
                                fill_timestamp=event_message_data["timestamp"] * 1e-3,
                            )
                            print(f'trade update {trade_update}')
                            self._order_tracker.process_trade_update(trade_update)

                    tracked_order = self._order_tracker.all_updatable_orders.get(client_order_id)
                    if tracked_order is not None:
                        order_update = OrderUpdate(
                            trading_pair=tracked_order.trading_pair,
                            update_timestamp=event_message_data["timestamp"] * 1e-3,
                            new_state=CONSTANTS.ORDER_STATE[event_message_data["type"]],
                            client_order_id=client_order_id,
                            exchange_order_id=str(event_message_data["orderId"]),
                        )
                        self._order_tracker.process_order_update(order_update=order_update)
                
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().error("Unexpected error in user stream listener loop.", exc_info=True)
                await self._sleep(5.0)

    async def _update_order_fills_from_trades(self):
        """
        This is intended to be a backup measure to get filled events with trade ID for orders,
        in case Btse's user stream events are not working.
        NOTE: It is not required to copy this functionality in other connectors.
        This is separated from _update_order_status which only updates the order status without producing filled
        events, since Btse's get order endpoint does not return trade IDs.
        The minimum poll interval for order status is 10 seconds.
        """
        small_interval_last_tick = self._last_poll_timestamp / self.UPDATE_ORDER_STATUS_MIN_INTERVAL
        small_interval_current_tick = self.current_timestamp / self.UPDATE_ORDER_STATUS_MIN_INTERVAL
        long_interval_last_tick = self._last_poll_timestamp / self.LONG_POLL_INTERVAL
        long_interval_current_tick = self.current_timestamp / self.LONG_POLL_INTERVAL

        if (long_interval_current_tick > long_interval_last_tick
                or (self.in_flight_orders and small_interval_current_tick > small_interval_last_tick)):
            query_time = int(self._last_trades_poll_btse_timestamp * 1e6)            
            self._last_trades_poll_btse_timestamp = self._time_synchronizer.time()            
            order_by_exchange_id_map = {}
            for order in self._order_tracker.all_fillable_orders.values():
                order_by_exchange_id_map[order.exchange_order_id] = order

            tasks = []
            trading_pairs = self.trading_pairs
            for trading_pair in trading_pairs:
                params = {
                    "symbol": await self.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
                }                
                if self._last_poll_timestamp > 0:
                    params["startTime"] = query_time
                tasks.append(self._api_get(
                    path_url=CONSTANTS.MY_TRADES_PATH_URL,
                    params=params,
                    is_auth_required=True))
            self.logger().debug(f"trading_pairs{tasks}")
            self.logger().debug(f"Polling for order fills of {len(tasks)} trading pairs.")
            results = await safe_gather(*tasks, return_exceptions=True)            

            for trades, trading_pair in zip(results, trading_pairs):

                if isinstance(trades, Exception):
                    self.logger().network(
                        f"Error fetching trades update for the order {trading_pair}: {trades}.",
                        app_warning_msg=f"Failed to fetch trade update for {trading_pair}."
                    )
                    continue
                for trade in trades:
                    exchange_order_id = str(trade["orderId"])
                    if exchange_order_id in order_by_exchange_id_map:
                        # This is a fill for a tracked order
                        tracked_order = order_by_exchange_id_map[exchange_order_id]
                        fee = TradeFeeBase.new_spot_fee(
                            fee_schema=self.trade_fee_schema(),
                            trade_type=tracked_order.trade_type,
                            percent_token=trade["feeCurrency"],
                            flat_fees=[TokenAmount(amount=Decimal(trade["feeAmount"]), token=trade["feeCurrency"])]
                        )
                        trade_update = TradeUpdate(
                            trade_id=str(trade["tradeId"]),
                            client_order_id=tracked_order.client_order_id,
                            exchange_order_id=exchange_order_id,
                            trading_pair=trading_pair,
                            fee=fee,
                            fill_base_amount=Decimal(trade["size"]),
                            fill_quote_amount=Decimal(trade["filledSize"]),
                            fill_price=Decimal(trade["filledPrice"]),
                            fill_timestamp=trade["timestamp"] * 1e-3,
                        )
                        self._order_tracker.process_trade_update(trade_update)
                    elif self.is_confirmed_new_order_filled_event(str(trade["tradeId"]), exchange_order_id, trading_pair):
                        # This is a fill of an order registered in the DB but not tracked any more
                        self._current_trade_fills.add(TradeFillOrderDetails(
                            market=self.display_name,
                            exchange_trade_id=str(trade["tradeId"]),
                            symbol=trading_pair))
                        self.trigger_event(
                            MarketEvent.OrderFilled,
                            OrderFilledEvent(
                                timestamp=float(trade["timestamp"]) * 1e-3,
                                order_id=self._exchange_order_ids.get(str(trade["orderId"]), None),
                                trading_pair=trading_pair,
                                trade_type=TradeType.BUY if trade["side"] == CONSTANTS.SIDE_BUY else TradeType.SELL,
                                order_type=trade["orderType"],
                                price=Decimal(trade["price"]),
                                amount=Decimal(trade["size"]),
                                trade_fee=DeductedFromReturnsTradeFee(
                                    flat_fees=[
                                        TokenAmount(
                                            trade["feeCurrency"],
                                            Decimal(trade["feeAmount"])
                                        )
                                    ]
                                ),
                                exchange_trade_id=str(trade["tradeId"])
                            ))
                        self.logger().info(f"Recreating missing trade in TradeFill: {trade}")

    async def _all_trade_updates_for_order(self, order: InFlightOrder) -> List[TradeUpdate]:
        trade_updates = []        
        if order.exchange_order_id is not None:
            exchange_order_id = order.exchange_order_id
            trading_pair = await self.exchange_symbol_associated_to_pair(trading_pair=order.trading_pair)            
            all_fills_response = await self._api_get(
                path_url=CONSTANTS.MY_TRADES_PATH_URL,
                params={
                    "symbol": trading_pair,
                    "orderId": exchange_order_id
                },
                is_auth_required=True,
                limit_id=CONSTANTS.MY_TRADES_PATH_URL)
            for trade in all_fills_response:
                exchange_order_id = str(trade["orderId"])
                fee = TradeFeeBase.new_spot_fee(
                    fee_schema=self.trade_fee_schema(),
                    trade_type=order.trade_type,
                    percent_token=trade["feeCurrency"],
                    flat_fees=[TokenAmount(amount=Decimal(trade["feeAmount"]), token=trade["feeCurrency"])]
                )
                trade_update = TradeUpdate(
                    trade_id=str(trade["tradeId"]),
                    client_order_id=order.client_order_id,
                    exchange_order_id=exchange_order_id,
                    trading_pair=trading_pair,
                    fee=fee,
                    fill_base_amount=Decimal(trade["size"]),
                    fill_quote_amount=Decimal(trade["filledSize"]),
                    fill_price=Decimal(trade["filledPrice"]),
                    fill_timestamp=trade["timestamp"] * 1e-3,
                )
                trade_updates.append(trade_update)

        return trade_updates

    async def _request_order_status(self, tracked_order: InFlightOrder) -> OrderUpdate:
        trading_pair = await self.exchange_symbol_associated_to_pair(trading_pair=tracked_order.trading_pair)
        updated_order_data = await self._api_get(
            path_url=CONSTANTS.OPEN_ORDER_URL,
            params={
                "symbol": trading_pair,
                "clOrderID": tracked_order.client_order_id
                },
            is_auth_required=True)
        updated_order_data = updated_order_data[0]
        new_state = CONSTANTS.ORDER_STATE[updated_order_data["orderState"]]

        order_update = OrderUpdate(
            client_order_id=tracked_order.client_order_id,
            exchange_order_id=str(updated_order_data["orderID"]),
            trading_pair=tracked_order.trading_pair,
            update_timestamp=updated_order_data["timestamp"] * 1e-3,
            new_state=new_state,
        )

        return order_update

    async def _update_balances(self):
        local_asset_names = set(self._account_balances.keys())
        remote_asset_names = set()

        wallet_balances = await self._api_get(
            path_url=CONSTANTS.ACCOUNTS_PATH_URL,
            is_auth_required=True)
        
        for balance_entry in wallet_balances:
            asset_name = balance_entry["currency"]
            free_balance = Decimal(balance_entry["available"])
            total_balance = Decimal(balance_entry["total"])
            self._account_available_balances[asset_name] = free_balance
            self._account_balances[asset_name] = total_balance
            remote_asset_names.add(asset_name)

        asset_names_to_remove = local_asset_names.difference(remote_asset_names)
        for asset_name in asset_names_to_remove:
            del self._account_available_balances[asset_name]
            del self._account_balances[asset_name]

    def _initialize_trading_pair_symbols_from_exchange_info(self, exchange_info: Dict[str, Any]):        
        mapping = bidict()        
        # with open('test.txt', 'w') as file:
        #         file.write(str(exchange_info))
        for symbol_data in filter(btse_utils.is_exchange_information_valid, exchange_info):            
            if "_" not in symbol_data["symbol"]:
                mapping[symbol_data["symbol"]] = combine_to_hb_trading_pair(base=symbol_data["base"],
                                                                        quote=symbol_data["quote"])
        self._set_trading_pair_symbol_map(mapping)

    async def _get_last_traded_price(self, trading_pair: str) -> float:
        params = {
            "symbol": await self.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
        }        
        resp_json = await self._api_request(
            method=RESTMethod.GET,
            path_url=CONSTANTS.TICKER_PRICE_CHANGE_PATH_URL,
            params=params
        )        
        return float(resp_json[0]["lastPrice"])
