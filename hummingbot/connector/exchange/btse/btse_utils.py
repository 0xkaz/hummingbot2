from decimal import Decimal
from typing import Any, Dict

from pydantic import Field, SecretStr

from hummingbot.client.config.config_data_types import BaseConnectorConfigMap, ClientFieldData
from hummingbot.core.data_type.trade_fee import TradeFeeSchema

CENTRALIZED = True
EXAMPLE_PAIR = "BTC-USD"

DEFAULT_FEES = TradeFeeSchema(
    maker_percent_fee_decimal=Decimal("0.002"),
    taker_percent_fee_decimal=Decimal("0.002"),
    buy_percent_fee_deducted_from_returns=True
)


def is_exchange_information_valid(exchange_info: Dict[str, Any]) -> bool:
    """
    Verifies if a trading pair is enabled to operate with based on its exchange information
    :param exchange_info: the exchange information for a trading pair
    :return: True if the trading pair is enabled, False otherwise
    """
    return exchange_info.get("active") is True and exchange_info.get("futures") is False


class BtseConfigMap(BaseConnectorConfigMap):
    connector: str = Field(default="btse", const=True, client_data=None)
    btse_api_key: SecretStr = Field(
        default=...,
        client_data=ClientFieldData(
            prompt=lambda cm: "Enter your Btse API key",
            is_secure=True,
            is_connect_key=True,
            prompt_on_new=True,
        )
    )
    btse_api_secret: SecretStr = Field(
        default=...,
        client_data=ClientFieldData(
            prompt=lambda cm: "Enter your Btse API secret",
            is_secure=True,
            is_connect_key=True,
            prompt_on_new=True,
        )
    )

    class Config:
        title = "btse"


KEYS = BtseConfigMap.construct()

OTHER_DOMAINS = ["btse_testnet"]
OTHER_DOMAINS_PARAMETER = {"btse_testnet": "btse_testnet"}
OTHER_DOMAINS_EXAMPLE_PAIR = {"btse_testnet": "BTC-USD"}
OTHER_DOMAINS_DEFAULT_FEES = {"btse_testnet": DEFAULT_FEES}


class BtseTestnetConfigMap(BaseConnectorConfigMap):
    connector: str = Field(default="btse_testnet", const=True, client_data=None)
    btse_api_key: SecretStr = Field(
        default=...,
        client_data=ClientFieldData(
            prompt=lambda cm: "Enter your Btse Testnet API key",
            is_secure=True,
            is_connect_key=True,
            prompt_on_new=True,
        )
    )
    btse_api_secret: SecretStr = Field(
        default=...,
        client_data=ClientFieldData(
            prompt=lambda cm: "Enter your Btse Testnet API secret",
            is_secure=True,
            is_connect_key=True,
            prompt_on_new=True,
        )
    )

    class Config:
        title = "btse_testnet"


OTHER_DOMAINS_KEYS = {"btse_testnet": BtseTestnetConfigMap.construct()}
