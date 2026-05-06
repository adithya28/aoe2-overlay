# SPDX-License-Identifier: AGPL-3.0-or-later
import win32api
from steam.guard import SteamAuthenticator

import asyncio  # noqa
import asyncio_gevent  # noqa
import logging

from steam.client import SteamClient  # noqa

import aiohttp.client_exceptions  # noqa
from aiohttp import ClientSession, web  # noqa
import base64  # noqa
from dataclasses import dataclass, field  # noqa
from steam.enums import EResult
from dotenv import load_dotenv  # noqa
import json  # noqa
import multidict  # noqa
import os  # noqa
import sys  # noqa
import time  # noqa
import datetime  # noqa

import subprocess  # noqa

from constants import ENCODED_TICKET_INTERVAL, RELIC_SESSION_UPDATE_INTERVAL
from flags import is_logged_in

# GAME IDs
APPID = (813780, "age2")

load_dotenv()


def get_package_dir():
    try:
        # get package directory, when run from poetry
        return __path__[0]
    except NameError:
        # get package directory, when run directly
        return os.getcwd()


@dataclass
class AppTicket:
    ticket: str
    last_update: int = field(default_factory=lambda: int(time.time()))


@dataclass
class RelicLinkSession:
    session_id: str
    last_update: int = field(default_factory=lambda: int(time.time()))


class TicketError(BaseException):
    pass


class LoginError(BaseException):
    pass


async def get_file_version(path=os.getenv("AOE2_PATH")):
    info = win32api.GetFileVersionInfo(path, '\\')
    ms = info['FileVersionMS']
    ls = info['FileVersionLS']
    return (win32api.HIWORD(ms), win32api.LOWORD(ms),
            win32api.HIWORD(ls), win32api.LOWORD(ls))


async def get_app_binary_checksum():
    app_version_info = await get_file_version()
    app_checksum = (app_version_info[1] - app_version_info[0]) * 65536 + app_version_info[
        2]  # (minor-major)*65536+build
    return app_checksum


def _setup_logging() -> logging.Logger:
    logger = logging.getLogger("RelicLinkProxy")
    logger.setLevel(logging.INFO)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    ch.setFormatter(formatter)

    logger.addHandler(ch)

    return logger


class RelicLinkProxy:
    def __init__(
            self, account_name, password, api_key_json_path=None, host="https://aoe-api.worldsedgelink.com/",
            steam_secrets_path=None
    ):
        # set up logging
        self.app_binary_checksum = None
        self.logger = _setup_logging()

        # set up steam
        self.steam = None
        self.steam_account_name = account_name
        self.steam_password = password
        self.shared_secret = os.getenv("STEAM_SHARED_SECRET")

        # set up proxy
        with open(api_key_json_path or os.path.join(get_package_dir(), "api_keys.json")) as api_keys_file:
            self.api_keys = json.load(api_keys_file).values()

        with open(steam_secrets_path or os.path.join(get_package_dir(), "steam_secrets.json")) as steam_secrets_file:
            self.steam_secrets = json.load(steam_secrets_file)

        self.webapp = None
        self.app_ticket = None
        self.relic_session = None
        self.http = ClientSession(host)

    async def steam_login(self):
        """Login to Steam with automatic 2FA"""
        try:
            self.logger.info(f"Logging into Steam as {self.steam_account_name}")
            self.app_binary_checksum = await get_app_binary_checksum()
            self.steam = SteamClient()
            sa = SteamAuthenticator(secrets=self.steam_secrets)
            mfa_code = sa.get_code()
            if self.steam is not None:
                result = self.steam.login(
                    username=self.steam_account_name,
                    password=self.steam_password,
                    two_factor_code=mfa_code
                )

                if result != EResult.OK:
                    self.logger.error(f"Steam login failed with result: {result}")

                self.logger.info("Steam login successful")

        except Exception as e:
            self.logger.error(f"Exception during Steam Initializing: {e}")
            return False

    async def get_encoded_ticket(self):
        if self.app_ticket is None or time.time() > self.app_ticket.last_update + ENCODED_TICKET_INTERVAL:
            # get an app ticket from steam, serialize it and base64 encode it
            try:

                self.app_ticket = AppTicket(
                    base64.standard_b64encode(
                        self.steam.get_encrypted_app_ticket(
                            APPID[0], userdata=b"RLINK"
                        ).encrypted_app_ticket.SerializeToString(deterministic=True)
                    ).decode()
                )
                self.logger.info("[Relic Login] Refreshed app ticket")

            except AttributeError as exc:
                self.logger.info("[Relic Login] Could not get encrypted app ticket from steam")
                raise TicketError() from exc

    async def relic_login(self):
        if (
                self.relic_session is None
                or time.time() > self.relic_session.last_update + RELIC_SESSION_UPDATE_INTERVAL
        ):
            try:
                if self.app_ticket is not None and self.steam is not None and self.steam.steam_id is not None and self.steam.user.name is not None:
                    self.logger.info("[Relic Login] attempting login")
                    login_request = await self.http.post(
                        "/game/login/platformlogin",
                        data={
                            "accountType": "STEAM",
                            "activeMatchId": "-1",
                            "alias": str(self.steam.user.name),
                            "appID": str(APPID[0]),
                            "auth": self.app_ticket.ticket,
                            "callNum": "0",
                            "clientLibVersion": "169",
                            "connect_id": "",
                            "country": "US",
                            "installationType": "windows",
                            "language": "en",
                            "lastCallTime": "33072262",
                            "macAddress": "57-4F-4C-4F-4C-4F",
                            "majorVersion": "4.0.0",
                            "minorVersion": "0",
                            "platformUserID": str(self.steam.steam_id.as_64),
                            "startGameToken": "",
                            "syncHash": "[3705476802, 2905248376]",
                            "timeoutOverride": "0",
                            "title": str(APPID[1]),
                        },
                    )

                    content = await login_request.text()

                    if f"/steam/{self.steam.steam_id.as_64}" in content:
                        self.logger.info("[Relic Login] Refreshed session")
                        data = await login_request.json()
                        self.relic_session = RelicLinkSession(data[1])
                        is_logged_in.set()
                    else:
                        self.logger.info("[Relic Login] Relic login failed")
                        is_logged_in.clear()
                        raise LoginError()
                else:
                    self.logger.info("[Relic Login] Steam not logged in")
                    raise LoginError(
                        "Some required data is missing. Steam not logged in."
                    )

            except (aiohttp.client_exceptions.ClientError, IndexError) as exc:
                self.logger.info("[Relic Login] Relic login failed")
                raise LoginError() from exc

    async def update_token(self):
        while True:
            try:
                await self.get_encoded_ticket()
                # Wait 5 seconds and try again
                await asyncio.sleep(5)
                await self.relic_login()
            except (TicketError, LoginError, asyncio.CancelledError) as exc:
                self.logger.error("[Relic Login] Relic login failed", exc_info=exc)
                return

    async def dot(self, _):
        data = {}
        if self.app_ticket is not None:
            data.update(
                {
                    "encrypted_app_token": {
                        "last_update": self.app_ticket.last_update,
                        "utc_string": datetime.datetime.fromtimestamp(
                            self.app_ticket.last_update, tz=datetime.timezone.utc
                        ).isoformat(),
                    }
                }
            )

        if self.relic_session is not None:
            data.update(
                {
                    "relic_session": {
                        "last_update": self.relic_session.last_update,
                        "utc_string": datetime.datetime.fromtimestamp(
                            self.relic_session.last_update, tz=datetime.timezone.utc
                        ).isoformat(),
                    }
                }
            )
        return web.json_response(data=data)

    async def forward_request(self, request):
        # Create a mutable copy of headers
        mutable_headers = dict(request.headers)

        api_key = mutable_headers.get("api_key")
        if api_key not in self.api_keys:
            mutable_headers["api_key"] = list(self.api_keys)[0]

        endpoint = request.match_info.get("endpoint")
        self.logger.info(f"{request.method} {endpoint}")

        excluded_headers = [
            "content-encoding",
            "content-length",
            "transfer-encoding",
            "connection",
            "api_key",
            "host",
            "user-agent",
        ]

        # Build filtered headers list from mutable copy
        headers = [
            (name, value)
            for (name, value) in mutable_headers.items()
            if name.lower() not in excluded_headers
        ]

        if request.method == "GET":
            # Convert query parameters to mutable MultiDict
            data = multidict.MultiDict(request.rel_url.query)

            data.update(
                {
                    "callNum": 0,
                    "connect_id": self.relic_session.session_id,
                    "lastCallTime": time.time(),
                    "sessionID": self.relic_session.session_id,
                    "appBinaryChecksum": self.app_binary_checksum,
                    "dataChecksum": 0,
                    "modDLLChecksum": 0,
                    "modDLLFile": "INVALID",
                    "modName": "INVALID",
                    "modVersion": "INVALID",
                    "versionFlags": 56950784
                }
            )

            self.logger.info(f"Request Headers: {str(headers)}")
            self.logger.info(f"Request Data: {str(data)}")

            # Make the request
            response = await self.http.get(f"/{endpoint}", params=data, headers=headers)
            self.logger.info(f"Response Headers: {str(response.headers)}")
            return web.Response(text=await response.text())
        elif request.method == "POST":
            data = multidict.MultiDict(await request.post())
            data.update(
                {
                    "callNum": 0,
                    "connect_id": self.relic_session.session_id,
                    "lastCallTime": time.time(),
                    "sessionID": self.relic_session.session_id,
                    "appBinaryChecksum": self.app_binary_checksum,
                    "dataChecksum": 0,
                    "modDLLChecksum": 0,
                    "modDLLFile": "INVALID",
                    "modName": "INVALID",
                    "modVersion": "INVALID",
                    "versionFlags": 56950784
                }
            )

            self.logger.info(f"Request Headers1: {str(headers)}")
            self.logger.info(f"Request Data: {str(data)}")
            response = await self.http.post(f"/{endpoint}", data=data, headers=headers)
            self.logger.info(f"Response Headers: {str(response.headers)}")
            return web.Response(text=await response.text())
        else:
            return web.Response(text="Method not allowed", status=405)

    async def run_server(self):
        app = web.Application()
        app.add_routes(
            [
                web.route("*", "/relic", self.dot),
                web.route("*", "/relic/{endpoint:[^{}]+}", self.forward_request),
            ]
        )

        self.webapp = web.AppRunner(app)
        try:
            await self.webapp.setup()
            site = web.TCPSite(self.webapp, "0.0.0.0", 5000)
            await site.start()
            self.logger.info("[aiohttp Server] Site started")

            while True:
                await asyncio.sleep(
                    3600
                )
        except asyncio.CancelledError:
            await self.webapp.cleanup()
            return


async def run():
    # Environment
    account_name = os.getenv("STEAM_ACCOUNT_NAME")
    password = os.getenv("STEAM_PASSWORD")
    api_key_json_path = os.getenv("API_KEY_JSON_PATH")

    if not account_name or not password:
        print("Account data missing. Please set your account data in the .env file!")
        sys.exit(1)

    proxy = RelicLinkProxy(account_name, password, api_key_json_path)
    await proxy.steam_login()
    await asyncio.gather(*[proxy.run_server(), proxy.update_token()])
