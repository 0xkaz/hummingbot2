from hummingbot.core.api_throttler.data_types import LinkedLimitWeightPair, RateLimit
from hummingbot.core.data_type.in_flight_order import OrderState

DEFAULT_DOMAIN = "btse_main"

HBOT_ORDER_ID_PREFIX = "HBOT"
MAX_ORDER_ID_LEN = 32

# Base URL
REST_URLS = {
    "btse_main": "https://api.btse.com",
    "btse_testnet": "https://testapi.btse.io"
}

WSS_URL = {
    "btse_main": "wss://ws.btse.com/ws/spot",
    "btse_testnet": "wss://testws.btse.io/ws/spot"
}

WSS_URL_OB = {
    "btse_main": "wss://ws.btse.com/ws/oss/spot",
    "btse_testnet": "wss://testws.btse.io/ws/oss/spot"
}

PUBLIC_API_VERSION = "v3"
PRIVATE_API_VERSION = "v3"

# Public API endpoints or BinanceClient function
TICKER_PRICE_CHANGE_PATH_URL = "/spot/api/v3.2/price"
EXCHANGE_INFO_PATH_URL = "/spot/api/v3.2/market_summary"
PING_PATH_URL = "/spot/api/v3.2/time"
SNAPSHOT_PATH_URL = "/spot/api/v3.2/orderbook/L2"
SERVER_TIME_PATH_URL = "/spot/api/v3.2/time"

# Private API endpoints or BinanceClient functione
ACCOUNTS_PATH_URL = "/spot/api/v3.2/user/wallet"
MY_TRADES_PATH_URL = "/spot/api/v3.2/user/trade_history"
ORDER_PATH_URL = "/spot/api/v3.2/order"
OPEN_ORDER_URL = "/spot/api/v3.2/user/open_orders"

BTSE_USER_STREAM_PATH_URL = "/ws/spot"

WS_HEARTBEAT_TIME_INTERVAL = 30

# Btse params

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"

TIME_IN_FORCE_GTC = "GTC"  # Good till cancelled
TIME_IN_FORCE_IOC = "IOC"  # Immediate or cancel
TIME_IN_FORCE_FOK = "FOK"  # Fill or kill


# Websocket event types
DIFF_EVENT_TYPE = "update"
TRADE_EVENT_TYPE = "tradeHistoryApi"
SNAPSHOT_EVENT_TYPE = "snapshotL1"

# Order States
ORDER_STATE = {
    "ORDER_INSERTED": 2,
    "ORDER_FULLY_TRANSACTED": 4,
    "ORDER_PARTIALLY_TRANSACTED": 5,
    "ORDER_CANCELLED": 6,
    "ORDER_REFUNDED": 7,
    "ORDER_REJECTED": 15,
    "ORDER_NOTFOUND": 16,
    "INSUFFICIENT_BALANCE": 8,
    "TRIGGER_INSERTED": 9,
    "TRIGGER_ACTIVATED": 10,
    "REQUEST_FAILED": 17,
    "STATUS_INACTIVE": OrderState.CANCELED,
    "STATUS_ACTIVE": OrderState.OPEN
}

# Rate Limit Type
ORDERS_PER_API = 'ORDERS_PER_API'
ORDERS_PER_USER = 'ORDERS_PER_USER'
QUERY_PER_API = 'QUERY_PER_API'
QUERY_PER_USER = 'QUERY_PER_USER'

REQUEST_WEIGHT = "REQUEST_WEIGHT"
ORDERS = "ORDERS"
ORDERS_24HR = "ORDERS_24HR"
RAW_REQUESTS = "RAW_REQUESTS"

# Rate Limit time intervals
ONE_MINUTE = 60
ONE_SECOND = 1
ONE_DAY = 86400

MAX_REQUEST = 5000

RATE_LIMITS = [
    # Pools
    RateLimit(limit_id=ORDERS_PER_API, limit=4500, time_interval=ONE_MINUTE),
    RateLimit(limit_id=ORDERS_PER_USER, limit=4500, time_interval=ONE_MINUTE),
    RateLimit(limit_id=QUERY_PER_API, limit=4500, time_interval=5 * ONE_MINUTE),
    RateLimit(limit_id=QUERY_PER_USER, limit=9000, time_interval= 5 * ONE_MINUTE),
    # Linked limits
    RateLimit(limit_id=TICKER_PRICE_CHANGE_PATH_URL, limit=MAX_REQUEST, time_interval=ONE_MINUTE,
              linked_limits=[LinkedLimitWeightPair(REQUEST_WEIGHT, 40),
                             LinkedLimitWeightPair(RAW_REQUESTS, 1)]),
    RateLimit(limit_id=EXCHANGE_INFO_PATH_URL, limit=MAX_REQUEST, time_interval=ONE_MINUTE,
              linked_limits=[LinkedLimitWeightPair(REQUEST_WEIGHT, 10),
                             LinkedLimitWeightPair(RAW_REQUESTS, 1)]),
    RateLimit(limit_id=SNAPSHOT_PATH_URL, limit=MAX_REQUEST, time_interval=ONE_MINUTE,
              linked_limits=[LinkedLimitWeightPair(REQUEST_WEIGHT, 50),
                             LinkedLimitWeightPair(RAW_REQUESTS, 1)]),
    RateLimit(limit_id=BTSE_USER_STREAM_PATH_URL, limit=MAX_REQUEST, time_interval=ONE_MINUTE,
              linked_limits=[LinkedLimitWeightPair(REQUEST_WEIGHT, 1),
                             LinkedLimitWeightPair(RAW_REQUESTS, 1)]),
    RateLimit(limit_id=SERVER_TIME_PATH_URL, limit=MAX_REQUEST, time_interval=ONE_MINUTE,
              linked_limits=[LinkedLimitWeightPair(REQUEST_WEIGHT, 1),
                             LinkedLimitWeightPair(RAW_REQUESTS, 1)]),
    RateLimit(limit_id=PING_PATH_URL, limit=MAX_REQUEST, time_interval=ONE_MINUTE,
              linked_limits=[LinkedLimitWeightPair(REQUEST_WEIGHT, 1),
                             LinkedLimitWeightPair(RAW_REQUESTS, 1)]),
    RateLimit(limit_id=ACCOUNTS_PATH_URL, limit=MAX_REQUEST, time_interval=ONE_MINUTE,
              linked_limits=[LinkedLimitWeightPair(REQUEST_WEIGHT, 10),
                             LinkedLimitWeightPair(RAW_REQUESTS, 1)]),
    RateLimit(limit_id=OPEN_ORDER_URL, limit=MAX_REQUEST, time_interval=ONE_MINUTE,
              linked_limits=[LinkedLimitWeightPair(REQUEST_WEIGHT, 10),
                             LinkedLimitWeightPair(RAW_REQUESTS, 1)]),
    RateLimit(limit_id=MY_TRADES_PATH_URL, limit=MAX_REQUEST, time_interval=ONE_MINUTE,
              linked_limits=[LinkedLimitWeightPair(REQUEST_WEIGHT, 10),
                             LinkedLimitWeightPair(RAW_REQUESTS, 1)]),
    RateLimit(limit_id=ORDER_PATH_URL, limit=MAX_REQUEST, time_interval=ONE_MINUTE,
              linked_limits=[LinkedLimitWeightPair(REQUEST_WEIGHT, 2),
                             LinkedLimitWeightPair(ORDERS, 1),
                             LinkedLimitWeightPair(ORDERS_24HR, 1),
                             LinkedLimitWeightPair(RAW_REQUESTS, 1)])
]

ORDER_NOT_EXIST_ERROR_CODE = 16
ORDER_NOT_EXIST_MESSAGE = "Order does not exist"
UNKNOWN_ORDER_ERROR_CODE = -2011
UNKNOWN_ORDER_MESSAGE = "Unknown order sent"
