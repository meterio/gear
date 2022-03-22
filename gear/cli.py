from os import times
from sys import getswitchinterval, exit


from jsonrpcserver import async_dispatch
import json
import asyncio
import websockets
import hashlib
import aiohttp
from aiohttp import web

from gear.utils.compat import meter_log_convert_to_eth_log

from .rpc import make_version
from json.decoder import JSONDecodeError
from .meter.account import (
    solo,
    keystore as _keystore,
)

from .utils.types import (

    encode_number
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

SUB_ID = '0x00640404976e52864c3cfd120e5cc28aac3f644748ee6e8be185fb780cdfd827'

async def checkHealth(request, logging=False, debug=False):
    r = {"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":8545}
    response = await async_dispatch(json.dumps(r), basic_logging=logging, debug=debug)
    return web.json_response(response.deserialized(), headers=res_headers, status=response.http_status)

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
        # request = await request.text()
        response = await async_dispatch(json.dumps(r), basic_logging=logging, debug=debug)
        if response.wanted:
            # print("Response #%s:"%(str(r['id'])), json.dumps(response.deserialized()))
            print("Response #%s: DONE"%(str(r['id'])))
            responses.append(json.loads(json.dumps(response.deserialized())))
        if response.http_status != 200:
            print("Error response #%s:" %(str(r['id'])), json.dumps(response.deserialized()))
            print("HTTP_STATUS: ", response.http_status)


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


BLOCK_FORMATTERS = {
   
   
    "timestamp": encode_number,
    "gasLimit": encode_number,
    "gasUsed": encode_number,
    "epoch":encode_number,
    "k":encode_number
    
}



def meter_block_convert_to_eth_block(block):
    # sha3Uncles, logsBloom, difficaulty, extraData are the required fields. nonce is optional
    n = block["nonce"]
    if n == 0:
        block["nonce"] = '0x0000000000000000'
    else:
        block["nonce"] = encode_number(n, 8)

    # sha3Uncles is always empty on meter
    block['sha3Uncles'] = '0x1dcc4de8dec75d7aab85b567b6ccd41ad312451b948a7413f0a142fd40d49347'
    # TODO: fix "fake" transactions root
    if len(block['transactions']) ==0:
        block['transactionsRoot'] = '0x56e81f171bcc55a6ff8345e692c0f86e5b48e01b996cadc001622fb5e363b421'
    #block['logsBloom'] = '0x00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000'
    block['difficulty'] = '0x'
    block['extraData'] = '0x'
    if 'kblockData' in block:
        del block['kblockData']
    if 'powBlocks' in block:
        del block['powBlocks']
    for key, value in block.items():
        if key in BLOCK_FORMATTERS:
           block[key] =  encode_number(value).decode()
    return block

    

newHeadListeners = {} # ws connection id -> ws connection
logListeners = {} # ws connection id -> { ws: ws connection, filters: filters }
# WSURL_NHEADS = 'ws://127.0.0.1:8669/subscriptions/beat'

def hash_digest(param):
    h = hashlib.sha224(param.encode())
    digest = h.hexdigest()
    return digest

async def run_new_head_observer(endpoint):
    ws_endpoint = endpoint.replace('https', 'ws').replace('http','ws')+'/subscriptions/beat'
    while True:
        try:
            async with websockets.connect(ws_endpoint) as beatWS:
                async for msg in beatWS:
                    # print('got: ', msg)
                    for key in list(newHeadListeners.keys()):
                        ws = newHeadListeners[key]
                        r = json.loads(msg)
                        if r.get("number"):
                            num = int(r.get("number"), 16)
                            print("forward block %d to conn %s" %(num,key))
                        else:
                            print('forward to conn', key)

                        r['timestamp']  = hex(r['timestamp'])
                        r['gasLimit'] = hex(r['gasLimit']).replace('0x0', '')
                        r['gasUsed'] = hex(r['gasUsed']).replace('0x0','')
                        r['nonce'] = hex(r['nonce']).replace('0x0', '0x0000000000000000')
                        try:
                            out = json.dumps({"jsonrpc": "2.0", "method":"eth_subscription" ,"params":{"subscription":SUB_ID, "result":r}})
                            await ws.send_str(out)
                        except Exception as e:
                            del newHeadListeners[key]
                            print('error happend for client ws', key, 'ignored', e)
        except Exception as e:
            print('error happend in head observer', e)
            print('retry in 10 seconds')
            await asyncio.sleep(10)

def match_filter(log, filters):
    for filter in filters:
        addressMatch = True
        topicsMatch = True
        if 'address' in filter:
            address = filter['address']
            if address != log['address']:
                addressMatch = False
    
        if 'topics' in filter and isinstance(filter['topics'], list) and len(filter['topics'])>0:
            topics = filter['topics']
            for index, topic in enumerate(topics):
                if len(log['topics']) < index+1 or topic and topic != log['topics'][index]:
                    topicsMatch = False
                    break
        if addressMatch and topicsMatch:
            return True
    return False
        

async def run_event_observer(endpoint):
    ws_endpoint = endpoint.replace('https', 'ws').replace('http','ws')+'/subscriptions/event'
    while True:
        try:
            async with websockets.connect(ws_endpoint) as eventWS:
                async for msg in eventWS:
                    # print('got: ', msg)
                    for key in list(logListeners.keys()):
                        info = logListeners[key]
                        ws = info['ws']
                        filters = info['filters']
                        log = json.loads(msg)
                        if not match_filter(log, filters):
                            print('not match filter, skip now', "key:", key)
                            continue
                        result = meter_log_convert_to_eth_log(log)
                        result['logIndex'] = result['logIndex'].decode('utf-8')
                        result['transactionIndex'] = result['transactionIndex'].decode('utf-8')
                        result['blockNumber'] = result['blockNumber'].decode('utf-8')
                        try:
                            out = json.dumps({"jsonrpc": "2.0", "method":"eth_subscription" ,"params":{"subscription":SUB_ID, "result":result}})
                            await ws.send_str(out)
                        except Exception as e:
                            del logListeners[key]
                            print('error happend for client ws', key, 'ignored', e)
        except Exception as e:
            print('error happend in event observer', e)
            print('log:', log)
            print('filters:', filters)
            print('retry in 10 seconds')
            await asyncio.sleep(10)

async def websocket_handler(request):
        headers = request.headers
        if (
            headers.get("connection", "").lower() == "upgrade"
            and headers.get("upgrade", "").lower() == "websocket"
            
        ):
        
            ws = web.WebSocketResponse()
            try:
                 await ws.prepare(request)
                 key = request.headers.get("sec-websocket-key", "")
                 async for msg in ws:
                    # print("REQ: ", msg.data)
                    if msg.type == aiohttp.WSMsgType.TEXT and msg.data.strip():
                        # if is a valid json request
                        jreq = json.loads(msg.data)

                        # handle batch requests
                        if isinstance(jreq, list):
                            ress = []
                            for r in jreq:
                                res = await handleRequest(r, False, False)
                                ress.append(json.loads(res))
                            await ws.send_str(json.dumps(ress))
                            continue

                        if 'method' not in jreq or 'id' not in jreq:
                            # not a valid json request
                            continue
                        
                        id = jreq['id']

                        if jreq['method'] == "eth_subscribe":
                            # handle subscribe
                            if isinstance(jreq['params'], list):
                                params = jreq['params']
                                if params[0] == 'newHeads':
                                    if key in newHeadListeners:
                                        continue
                                    newHeadListeners[key] = ws
                                    print("SUBSCRIBE to newHead")
                                    #send a subscription id to the client
                                    await ws.send_str(json.dumps({"jsonrpc": "2.0" ,"result":SUB_ID, "id":id}))
                                
                                if params[0] == 'logs':
                                    if key in logListeners:
                                        continue
                                    logListeners[key] = {"ws":ws, "filters":params[1:]}
                                    print("SUBSCRIBE to logs", "filters:", params[1:])
                                    await ws.send_str(json.dumps({"jsonrpc": "2.0" ,"result":SUB_ID, "id":id}))

                            #begin subscription
                            # while True:
                            
                            #     res = await handleRequest( json.loads(msg.data), False, False)
                            #     copy_obj = copy.deepcopy(json.loads(res))
                            #     # convert the subscription object into an appropriate response
                            #     result = meter_block_convert_to_eth_block(copy_obj['result'])
                                
                            #     res_obj = {"jsonrpc": copy_obj["jsonrpc"] , "method":"eth_subscription", "params":{"result":result, "subscription":SUB_ID}}
                            #     await ws.send_str(json.dumps(res_obj))
                        elif (jreq['method'] == "eth_unsubscribe"):
                            # handle unsubscribe
                            await ws.send_str(json.dumps({"jsonrpc": "2.0" ,"result":True, "id":id}))
                            if key in newHeadListeners:
                                del newHeadListeners[key]
                            if key in logListeners:
                                del logListeners[key]
                            print("UNSUBSCRIBE key:", key)
                            await ws.close()
                        else:
                            # handle normal requests
                            res = await handleRequest(json.loads(msg.data), False, False)
                            print("Forward response to ws conn %s" % key)
                            await ws.send_str(res)
                            # await ws.send_str(json.dumps({"jsonrpc":"2.0", "result":json.loads(res), "id":count}))
                        
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await ws.send_str(msg.data)
                        
                    elif msg.type == aiohttp.WSMsgType.PING:
                        await ws.ping()
                        
                    elif msg.type == aiohttp.WSMsgType.PONG:
                        await ws.pong()
                        
                    elif ws.closed:
                        if key not in newHeadListeners:
                            return
                        del newHeadListeners[key]
                        await ws.close(code=ws.close_code, message=msg.extra)
                    else:
                        print("unknown REQ: ", msg)
                        pass
                        # await ws.send_str(json.dumps({"jsonrpc": "2.0" ,"result":"", "id":count}))
            
            except Exception as e:
                print("ERROR HAPPENED:", e)
                await ws.close()
        else:
            # return await handleRequest(request, False, False)
            pass
           

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
        "/health", lambda r: checkHealth(r,log,debug))
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

    head_observer = asyncio.create_task(run_new_head_observer(endpoint))
    event_observer = asyncio.create_task(run_event_observer(endpoint))
    await head_observer
    # while True:
        # await asyncio.sleep(3600)  # sleep forever


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