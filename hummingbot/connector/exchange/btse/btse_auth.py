import hashlib
import hmac
import time
from typing import Dict

from hummingbot.connector.time_synchronizer import TimeSynchronizer
from hummingbot.core.web_assistant.auth import AuthBase
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, RESTRequest, WSRequest


class BtseAuth(AuthBase):
    def __init__(self, api_key: str, secret_key: str, time_provider: TimeSynchronizer):
        self.api_key = api_key
        self.secret_key = secret_key
        self.time_provider = time_provider

    async def rest_authenticate(self, request: RESTRequest) -> RESTRequest:
        """
        Adds the server time and the signature to the request, required for authenticated interactions. It also adds
        the required parameter in the request header.
        :param request: the request to be configured for authenticated interaction
        """
        headers = {}
        if request.headers is not None:
            headers.update(request.headers)
        headers.update(self.header_for_authentication(request))        
        request.headers = headers

        return request

    async def ws_authenticate(self, request: WSRequest) -> WSRequest:
        """
        This method is intended to configure a websocket request to be authenticated. Btse does not use this
        functionality
        """
        return request  # pass-through
    
    def generate_ws_authentication_message(self):
        """
        Generates the authentication message to start receiving messages from
        the 3 private ws channels
        """
        expires = int((self.time_provider.time() + 10) * 1e3)
        message = f"/ws/spot{expires}"
        signature = hmac.new(self.secret_key.encode("utf8"), message.encode("utf8"), hashlib.sha384).hexdigest()
        auth_message = {
            "op": "authKeyExpires",
            "args": [self.api_key, expires, signature]
        }
        return auth_message

    def header_for_authentication(self, request: RESTRequest) -> Dict[str, str]:
        lang = "latin-1"
        nonce = str(int(time.time() * 1000))
        path = request.url.replace("https://api.btse.com/spot", "").replace("https://testapi.btse.io/spot", "")
        # if request.params != None:
        #     path += '?' + str(request.params)
        message = path + nonce
        if request.method == RESTMethod.POST:
            message += str(request.data)        
        signature = hmac.new(bytes(self.secret_key, lang), msg=bytes(message, lang), digestmod=hashlib.sha384).hexdigest()
        headers = {
            "request-api": self.api_key,
            "request-nonce": nonce,
            "request-sign": signature
        }
        return headers
