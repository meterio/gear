import copy
from sys import getswitchinterval, exit

from jsonrpcserver import async_dispatch
import json
import asyncio
import websockets
import aiohttp
from aiohttp import web

from .rpc import make_version
from json.decoder import JSONDecodeError
from .meter.account import (
    solo,
    keystore as _keystore,
)
from .meter.client import meter
import requests
import click
from datetime import datetime


res_headers = {
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Allow-Origin": "*",
    "Connection": "keep-alive",
}



async def handle(request, logging=False, debug=False):
    jreq = await request.json()
    reqStr = json.dumps(jreq)
    arrayNeeded = True
    if not isinstance(jreq, list):
        jreq = [jreq]
        arrayNeeded = False

    responses = []
    print('\n'+'-'*40)
    print("call: [%s] ts:%.0f"% (jreq[0]['method'] if jreq and len(jreq)>=1 else 'unknown', datetime.now().timestamp()),
              "\nRequest:", reqStr)
    for r in jreq:
        method = r['method']
        # request = await request.text()
        response = await async_dispatch(json.dumps(r), basic_logging=logging, debug=debug)
        if response.wanted:
            print("Response #%s:"%(str(r['id'])), json.dumps(response.deserialized()))
            responses.append(json.loads(json.dumps(response.deserialized())))

    print("-"*40)
    if len(responses):
        if arrayNeeded:
            return web.json_response(responses, headers=res_headers, status=response.http_status)
        else:
            return web.json_response(responses[0], headers=res_headers, status=response.http_status)
    else:
        return web.Response(headers=res_headers, content_type="text/plain")









async def handleRequest(request, logging=False, debug=False):
   
    jreq = request
    
    reqStr = json.dumps(jreq)
    arrayNeeded = True
    if not isinstance(jreq, list):
        jreq = [jreq]
        arrayNeeded = False

    responses = []
    print('\n'+'-'*40)
    print("call: [%s] ts:%.0f"% (jreq[0]['method'] if jreq and len(jreq)>=1 else 'unknown', datetime.now().timestamp()),
              "\nRequest:", reqStr)
    for r in jreq:
        method = r['method']
        # request = await request.text()
        response = await async_dispatch(json.dumps(r), basic_logging=logging, debug=debug)
        if response.wanted:
           
            print("Response #%s:"%(str(r['id'])), json.dumps(response.deserialized()))
            responses.append(json.loads(json.dumps(response.deserialized())))
            

    print("-"*40)
    if len(responses):
        if arrayNeeded:
            return web.json_response(responses, headers=res_headers, status=response.http_status).text
           
            
        else:
            return web.json_response(responses[0], headers=res_headers, status=response.http_status,
             content_type='application/json', dumps=json.dumps
            ).text
           
    else:
        
        return web.Response(headers=res_headers, content_type="text/plain").text


async def websocket_handler(request):
        count = 1
        headers = request.headers
        if (
            headers.get("connection", "").lower() == "upgrade"
            and headers.get("upgrade", "").lower() == "websocket"
            
        ):
        
            ws = web.WebSocketResponse()
            try:
                 await ws.prepare(request)
                 async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT and msg.data != '\n' and msg.data.strip():
                        if(json.loads(msg.data)['method'] == "eth_subscribe"):
                            #send a subscription id to the client
                            await ws.send_str(json.dumps({"jsonrpc": "2.0" ,"result":"0x00640404976e52864c3cfd120e5cc28aac3f644748ee6e8be185fb780cdfd827", "id":count}))
                            count = count + 1
                            #begin subscription
                            while True:
                               
                                res = await handleRequest( json.loads(msg.data), False, False)
                                copy_obj = copy.deepcopy(json.loads(res))
                                copy_obj["result"]["timestamp"] = hex(copy_obj["result"]["timestamp"])
                                copy_obj["result"]["k"] = hex(copy_obj["result"]["k"])
                                copy_obj["result"]["gasLimit"] = hex(copy_obj["result"]["gasLimit"])
                                copy_obj["result"]["gasUsed"] = hex(copy_obj["result"]["gasUsed"])
                                copy_obj["result"]["nonce"] = hex(copy_obj["result"]["nonce"])
                                copy_obj["result"]["epoch"] = hex(copy_obj["result"]["epoch"])

                                # convert the subscription object into an appropriate response
                                res_obj = {"jsonrpc": copy_obj["jsonrpc"] , "method":"eth_subscription", "params":{"result":copy_obj["result"], "subscription":"0x00640404976e52864c3cfd120e5cc28aac3f644748ee6e8be185fb780cdfd827"}}
                                await ws.send_str(json.dumps(res_obj))
                        elif (json.loads(msg.data)['method'] == "eth_unsubscribe"):
                            # return 'true' for eth_unsubscribe
                            await ws.send_str(json.dumps({"jsonrpc": "2.0" ,"result":True, "id":count}))
                        else:
                            res = await handleRequest( json.loads(msg.data), False, False)
                            await ws.send_str(res)
                        
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await ws.send_str(msg.data)
                        
                    elif msg.type == aiohttp.WSMsgType.PING:
                        await ws.ping()
                        
                    elif msg.type == aiohttp.WSMsgType.PONG:
                        await ws.pong()
                        
                    elif ws.closed:
                        await ws.close(code=ws.close_code, message=msg.extra)
                    else:
                        await ws.send_str(json.dumps({"jsonrpc": "2.0" ,"result":"", "id":count}))
            
            except:
                await ws.close()
        else:
            return await handleRequest(request, False, False)
           

def get_http_app(host, port, endpoint, keystore, passcode, log, debug, chainid):
    try:
        response = requests.options(endpoint)
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        print("Unable to connect to Meter-Restful server.")
        return

    meter.set_endpoint(endpoint)
    meter.set_chainid(chainid)
    if keystore == "":
        meter.set_accounts(solo())
    else:
        meter.set_accounts(_keystore(keystore, passcode))

    app = web.Application()
    
    # app.router.add_get("/",lambda r:  websocket_handler(r))
    app.router.add_get("/", lambda r: web.Response(headers=res_headers))
    app.router.add_post("/", lambda r: handle(r, log, debug))
    app.router.add_options("/", lambda r: web.Response(headers=res_headers))
    app.router.add_get(
        "/health", lambda r: web.Response(headers=res_headers, body="OK", content_type="text/plain"))
    # web.run_app(app, host=host, port=port)
    return app


def get_ws_app(host, port, endpoint, keystore, passcode, log, debug, chainid):
    try:
        response = requests.options(endpoint)
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        print("Unable to connect to Meter-Restful server.")
        return

    meter.set_endpoint(endpoint)
    meter.set_chainid(chainid)
    if keystore == "":
        meter.set_accounts(solo())
    else:
        meter.set_accounts(_keystore(keystore, passcode))

    app = web.Application()
    
    app.router.add_get("/",lambda r:  websocket_handler(r))
    # app.router.add_get("/", lambda r: web.Response(headers=res_headers))
    # app.router.add_post("/", lambda r: handle(r, log, debug))
    app.router.add_options("/", lambda r: web.Response(headers=res_headers))
    app.router.add_get(
        "/health", lambda r: web.Response(headers=res_headers, body="OK", content_type="text/plain"))
    # web.run_app(app, host=host, port=port)
    return app


async def run_server(host, port, endpoint, keystore, passcode, log, debug, chainid):
    http_app = get_http_app(host, port, endpoint, keystore, passcode, log, debug, chainid)

    if http_app == None:
        print("Could not start http server due to connection problem, check your --endpoint settings")
        exit(-1)
    print('Starting http server')
    http_runner = web.AppRunner(http_app)
    await http_runner.setup()
    http = web.TCPSite(http_runner, host, port)
    await http.start()
    print("HTTP Listening on %s:%s" % (host, port))

    ws_app = get_ws_app(host, port, endpoint, keystore, passcode, log, debug, chainid)
    if ws_app == None:
        print("Could not start http server due to connection problem, check your --endpoint settings")
        exit(-1)
    print('Starting ws server')
    ws_runner = web.AppRunner(ws_app)
    await ws_runner.setup()
    ws = web.TCPSite(ws_runner, host, int(port)+1)
    await ws.start()
    print("Websocket Listening on %s:%s" % (host, int(port)+1))

    while True:
        await asyncio.sleep(3600)  # sleep forever


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
@click.option(
    "--chainid",
    default="0x53"
)
def main(host, port, endpoint, keystore, passcode, log, debug, chainid):
    asyncio.run(run_server(host, port, endpoint, keystore, passcode, log, debug, chainid))

    

if __name__ == '__main__':
    main()