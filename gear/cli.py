from jsonrpcserver import async_dispatch
from aiohttp import web
from .rpc import make_version
from .meter.account import (
    solo,
    keystore as _keystore,
)
from .meter.client import meter
import requests
import click
from datetime import datetime


res_headers = {
    "Access-Control-Allow-Headers": "Origin, X-Requested-With, Content-Type, Accept",
    "Access-Control-Allow-Origin": "*",
    "Connection": "keep-alive",
}


async def handle(request, logging=False, debug=False):
    jreq = await request.json()
    method = jreq['method']
    request = await request.text()
    response = await async_dispatch(request, basic_logging=logging, debug=debug)
    if response.wanted:
        print("-"*40+"\nRPC Call:", method, "\nTime: ", datetime.now().timestamp(), "\nRequest:", request, "\nResponse:",
              str(response.deserialized())+"\n"+"-"*40)
        return web.json_response(response.deserialized(), headers=res_headers, status=response.http_status)
    else:
        return web.Response(headers=res_headers, content_type="text/plain")


@click.command()
@click.option(
    "--host",
    default="127.0.0.1",
)
@click.option(
    "--port",
    default=8545,
    type=int,
)
@click.option(
    "--endpoint",
    default="http://127.0.0.1:8669",
)
@click.option(
    "--keystore",
    default="",
)
@click.option(
    "--passcode",
    default="",
)
@click.option(
    "--log",
    default=False,
    type=bool,
)
@click.option(
    "--debug",
    default=False,
    type=bool,
)
def run_server(host, port, endpoint, keystore, passcode, log, debug):
    print('run server')
    try:
        print(endpoint)
        response = requests.options(endpoint)
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        print("Unable to connect to Meter-Restful server.")
        return

    print(make_version())
    print("Listening on %s:%s" % (host, port))

    meter.set_endpoint(endpoint)
    if keystore == "":
        meter.set_accounts(solo())
    else:
        meter.set_accounts(_keystore(keystore, passcode))

    app = web.Application()
    app.router.add_post("/", lambda r: handle(r, log, debug))
    app.router.add_options("/", lambda r: web.Response(headers=res_headers))
    app.router.add_get(
        "/health", lambda r: web.Response(headers=res_headers, body="OK", content_type="text/plain"))
    web.run_app(app, host=host, port=port)


if __name__ == '__main__':
    run_server()
