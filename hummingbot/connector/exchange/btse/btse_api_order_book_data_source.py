import asyncio
import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from hummingbot.connector.exchange.btse import btse_constants as CONSTANTS, btse_web_utils as web_utils
from hummingbot.connector.exchange.btse.btse_order_book import BtseOrderBook
from hummingbot.core.data_type.order_book_message import OrderBookMessage
from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, WSJSONRequest
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.ws_assistant import WSAssistant
from hummingbot.logger import HummingbotLogger

if TYPE_CHECKING:
    from hummingbot.connector.exchange.btse.btse_exchange import BtseExchange


class BtseAPIOrderBookDataSource(OrderBookTrackerDataSource):
    HEARTBEAT_TIME_INTERVAL = 30.0
    TRADE_STREAM_ID = 1
    DIFF_STREAM_ID = 2
    ONE_HOUR = 60 * 60

    _logger: Optional[HummingbotLogger] = None

    def __init__(self,
                 trading_pairs: List[str],
                 connector: 'BtseExchange',
                 api_factory: WebAssistantsFactory,
                 domain: str = CONSTANTS.DEFAULT_DOMAIN):
        super().__init__(trading_pairs)
        self._connector = connector
        self._trade_messages_queue_key = CONSTANTS.TRADE_EVENT_TYPE
        self._diff_messages_queue_key = CONSTANTS.DIFF_EVENT_TYPE
        self._domain = domain
        self._api_factory = api_factory        

    async def get_last_traded_prices(self,
                                     trading_pairs: List[str],
                                     domain: Optional[str] = None) -> Dict[str, float]:        
        return await self._connector.get_last_traded_prices(trading_pairs=trading_pairs)

    async def _request_order_book_snapshot(self, trading_pair: str) -> Dict[str, Any]:
        """
        Retrieves a copy of the full order book from the exchange, for a particular trading pair.

        :param trading_pair: the trading pair for which the order book will be retrieved

        :return: the response from the exchange (JSON dictionary)
        """
        params = {
            "symbol": await self._connector.exchange_symbol_associated_to_pair(trading_pair=trading_pair),
            "limit": "1000"
        }
        rest_assistant = await self._api_factory.get_rest_assistant()
        snapshot_resp = await rest_assistant.execute_request(
            url=web_utils.public_rest_url(path_url=CONSTANTS.SNAPSHOT_PATH_URL, domain=self._domain),
            params=params,
            method=RESTMethod.GET,
            throttler_limit_id=CONSTANTS.SNAPSHOT_PATH_URL,
        )

        data = self._rebase_snapshot_obj(snapshot_resp)
        return data

    async def _subscribe_channels(self, ws: WSAssistant):
        """
        Subscribes to the trade events and diff orders events through the provided websocket connection.
        :param ws: the websocket assistant used to connect to the exchange
        """
        try:            
            depth_params = []
            for trading_pair in self._trading_pairs:
                symbol = await self._connector.exchange_symbol_associated_to_pair(trading_pair=trading_pair)                
                depth_params.append(f"update:{symbol}_0")
            payload = {
                "op": "subscribe",
                "args": depth_params,
            }
            subscribe_orderbook_request: WSJSONRequest = WSJSONRequest(payload=payload)
            await ws.send(subscribe_orderbook_request)                        
            self.logger().info("Subscribed to public order book and trade channels...")                    
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger().error(
                "Unexpected error occurred subscribing to order book trading and delta streams...",
                exc_info=True
            )
            raise

    async def listen_for_trades(self, ev_loop: asyncio.BaseEventLoop, output: asyncio.Queue):
        """
        Listen for trades using websocket trade channel
        """        
        while True:
            try:
                ws: WSAssistant = await self._api_factory.get_ws_assistant()
                await ws.connect(ws_url=CONSTANTS.WSS_URL[self._domain],
                                ping_timeout=CONSTANTS.WS_HEARTBEAT_TIME_INTERVAL)
                
                trade_params = []                
                for trading_pair in self._trading_pairs:
                    symbol = await self._connector.exchange_symbol_associated_to_pair(trading_pair=trading_pair)
                    trade_params.append(f"tradeHistoryApi:{symbol}")  
                payload = {
                    "op": "subscribe",
                    "args": trade_params,
                }
                subscribe_trade_request: WSJSONRequest = WSJSONRequest(payload=payload)                
                await ws.send(subscribe_trade_request)
                async for ws_response in ws.iter_messages():
                    msg = ws_response.data
                    await self._parse_trade_message(msg, output)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().error("Trades: Unexpected error with WebSocket connection. Retrying after 30 seconds...",
                                    exc_info=True)
                await self._sleep(CONSTANTS.WS_HEARTBEAT_TIME_INTERVAL)
            finally:
                await ws.disconnect()

    async def _connected_websocket_assistant(self) -> WSAssistant:
        ws: WSAssistant = await self._api_factory.get_ws_assistant()
        await ws.connect(ws_url=CONSTANTS.WSS_URL_OB[self._domain],
                         ping_timeout=CONSTANTS.WS_HEARTBEAT_TIME_INTERVAL)
        return ws

    def _rebase_snapshot_obj(self, snapshot: List[Dict[str, Any]]) -> Any:
        """
        Rebase the snapshot JSON object to the standard like binance response

        :param snapshot: The snapshot is the response from snaptshot api of btse

        :return: return standard JSON object
        """
        data: Dict[str, Any] = {
            'lastUpdateId': snapshot["timestamp"],
            'asks': [],
            'bids': []
        }
        for item in snapshot["sellQuote"]:
            data["asks"].append([item["price"], item["size"]])
        for item in snapshot["buyQuote"]:
            data["bids"].append([item["price"], item["size"]])
        return data

    async def _order_book_snapshot(self, trading_pair: str) -> OrderBookMessage:        
        snapshot: Dict[str, Any] = await self._request_order_book_snapshot(trading_pair)
        snapshot_timestamp: float = time.time()
        snapshot_msg: OrderBookMessage = BtseOrderBook.snapshot_message_from_exchange(
            snapshot,
            snapshot_timestamp,
            metadata={"trading_pair": trading_pair}
        )        
        return snapshot_msg
    
    async def _parse_trade_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):                    
        if 'data' in raw_message:
            for trade in raw_message["data"]:
                trading_pair = await self._connector.trading_pair_associated_to_exchange_symbol(symbol=trade["symbol"])                
                trade_message = BtseOrderBook.trade_message_from_exchange(
                    trade, {"trading_pair": trading_pair})                
                message_queue.put_nowait(trade_message)

    async def _parse_order_book_diff_message(self, raw_message: Dict[str, Any], message_queue: asyncio.Queue):            
            trading_pair = await self._connector.trading_pair_associated_to_exchange_symbol(symbol=raw_message["data"]["symbol"])
            order_book_message: OrderBookMessage = BtseOrderBook.diff_message_from_exchange(
                raw_message["data"], time.time(), {"trading_pair": trading_pair})            
            message_queue.put_nowait(order_book_message)

    def _channel_originating_message(self, event_message: Dict[str, Any]) -> str:
        channel = ""
        event_type = event_message.get("topic")
        if event_type == None:
            return self._trade_messages_queue_key
        channel = (self._diff_messages_queue_key if CONSTANTS.DIFF_EVENT_TYPE in event_type
                        else self._trade_messages_queue_key)
        return channel
