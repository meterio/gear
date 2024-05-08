from jsonrpcserver import Error, async_dispatch
import json
import asyncio
import websockets
import hashlib
import aiohttp
from aiohttp import web
import time
from lru import LRU
from gear import __version__

from gear.utils.compat import meter_log_convert_to_eth_log, meter_block_convert_to_eth_block
from json.decoder import JSONDecodeError
from .meter.account import (
    solo,
    keystore as _keystore,
)
import logging
import logging.config
from .log import LOGGING_CONFIG

from .utils.types import (
    encode_number
)
from .meter.client import meter
import requests
import click

from .rpc import get_block

res_headers = {
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Allow-Origin": "*",
    "Connection": "keep-alive",
}

logging.config.dictConfig(LOGGING_CONFIG)

logger = logging.getLogger('gear')

SUB_ID = '0x00640404976e52864c3cfd120e5cc28aac3f644748ee6e8be185fb780cdfd827'


newHeadListeners = {}  # ws connection id -> ws connection
logListeners = {}  # ws connection id -> { ws: ws connection, filters: filters }
receiptListeners = {} # txHash -> {ws: ws connection, id: rpcID, connID: ws connection id}
# WSURL_NHEADS = 'ws://127.0.0.1:8669/subscriptions/beat'


def hash_digest(param):
    h = hashlib.sha224(param.encode())
    digest = h.hexdigest()
    return digest

def genID(strkey):
    h = hashlib.sha256(strkey.encode())
    id = h.hexdigest()
    return '0x'+id

async def run_new_head_observer(endpoint):
    ws_endpoint = endpoint.replace('https', 'ws').replace(
        'http', 'ws')+'/subscriptions/beat'
    while True:
        try:
            async with websockets.connect(ws_endpoint) as beatWS:
                logger.info("subscribed to beat")
                async for msg in beatWS:
                    for key in list(newHeadListeners.keys()):
                        ws = newHeadListeners[key]
                        r = json.loads(msg)
                        if r.get("number"):
                            num = int(r.get("number"), 16)
                            logger.info("forward block %d to ws %s", num, key)
                        else:
                            logger.info('forward to ws %s', key)
                        blk = meter_block_convert_to_eth_block(r)
                        try:
                            subid = genID(key)
                            out = json.dumps({"jsonrpc": "2.0", "method": "eth_subscription", "params": {
                                             "subscription": subid, "result": blk}})
                            # logger.info('res: %s', out)
                            await ws.send_str(out)
                        except Exception as e:
                            del newHeadListeners[key]
                            logger.error(
                                'error happend for client ws %s, ignored: %s', key, e)
        except Exception as e:
            logger.error('error happened in beat observer: %s', e)
            logger.error('retry in 10 seconds')
            await asyncio.sleep(10)


def match_filter(log, filters):
    for filter in filters:
        addressMatch = False
        topicsMatch = False

        addrFilter = filter.get('address', None)
        if addrFilter is None:
            addressMatch = True
        elif isinstance(addrFilter, str):
            # exact match
            address = filter['address'].lower()
            if address == log['address'].lower():
                addressMatch = True
        elif isinstance(addrFilter, list):
            # 'or' options with array
            for addr in addrFilter:
                address = addr.lower()
                if address == log['address'].lower():
                    addressMatch = True
                    break

        topicFilter = filter.get('topics', [])
        if isinstance(topicFilter, list) and len(topicFilter) > 0:
            indexMatch = []
            for index, topic in enumerate(topicFilter):
                if topic is None:
                    indexMatch.append(True)
                elif isinstance(topic, str):
                    if len(log['topics']) >= index+1 and topic == log['topics'][index]:
                        indexMatch.append(True)
                    else:
                        indexMatch.append(False)
                elif isinstance(topic, list):
                    for ti, t in enumerate(topic):
                        if t is None:
                            indexMatch.append(True)
                        elif len(log['topics']) >= ti+1 and t == log['topics'][ti]:
                            indexMatch.append(True)
                        else:
                            indexMatch.append(False)
                else:
                    indexMatch.append(False)
            topicsMatch = all(indexMatch)
        else:
            topicsMatch = True

        if addressMatch and topicsMatch:
            return True
    return False



async def run_tx_observer():
    while True:
        for key in receiptListeners:
            try:
                info = receiptListeners[key]
                ws = info['ws']
                id = info['id']
                connID = info['connID']
                r = await meter.get_transaction_receipt(key)
                if r:
                    logger.info("forward receipt to ws %s", connID)
                    out = json.dumps({"jsonrpc": "2.0", "method": "eth_subscription", "result":r, "id":id})
                    await ws.send_str(out)
                    del receiptListeners[key]
            except Exception as e:
                del receiptListeners[key]
                logger.error('error happend: %s for client receipt ws: %s ', e, key) 
        await asyncio.sleep(1)


                    
async def run_event_observer(endpoint):
    ws_endpoint = endpoint.replace('https', 'ws').replace(
        'http', 'ws')+'/subscriptions/event'
    while True:
        try:
            async with websockets.connect(ws_endpoint) as eventWS:
                logger.info("subscribed to event")
                async for msg in eventWS:
                    # logger.info('got msg: %s', str(msg))
                    for key in list(logListeners.keys()):
                        info = logListeners[key]
                        ws = info['ws']
                        filters = info['filters']
                        log = json.loads(msg)
                        if not match_filter(log, filters):
                            logger.info(
                                'not match filter, skip now for key %s', key)
                            continue
                        result = meter_log_convert_to_eth_log(log)
                        result['logIndex'] = result['logIndex']
                        result['transactionIndex'] = result['transactionIndex']
                        result['blockNumber'] = result['blockNumber']
                        subid = genID(key)
                        try:
                            out = json.dumps({"jsonrpc": "2.0", "method": "eth_subscription", "params": {
                                             "subscription": subid, "result": result}})
                            await ws.send_str(out)
                        except Exception as e:
                            del logListeners[key]
                            logger.error(
                                'error happend: %s for client ws: %s ', e, key)
        except Exception as e:
            logger.error('error happend in event observer: %s', e)
            # logger.error('log: %s', log)
            # logger.error('filters: %s', filters)
            logger.error('retry in 10 seconds')
            await asyncio.sleep(10)


async def checkHealth():
    logger.info("check health")
    r = {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 8545}
    try:
        res = await handleTextRequest(json.dumps(r), 'Health', 'local')
        resJson = json.loads(res)

        if 'error' in resJson:
            return web.Response(text=res, status=500, content_type="application/json", headers=res_headers)
        return web.Response(text=res, content_type="application/json", headers=res_headers)

    except Exception as e:
        logger.error("error happened: %s", e)
        return web.Response(text=res, status=500, content_type="application/json", headers=res_headers)

def secondElem(items):
    return items[1] if items else 1

async def getReqs():
    global reqs
    return web.Response(text=json.dumps(reqs),  headers=res_headers)

async def getUsage():
    global reqs
    global usage
    global USAGE_LIMIT_WINDOW
    global USAGE_LIMIT
    global THROTTLE_ENABLED
    ips = {}
    methods = {}
    cacheds = {}
    throttleds = {}

    refreshReqs()

    for item in reqs:
        ip = item[1]
        method = item[2]
        cached = item[3]
        throttled = item[4]
        ips[ip] = ips.get(ip, 0) + 1
        methods[method] = methods.get(method, 0) + 1
        if cached:
            cacheds[method] = cacheds.get(method, 0) + 1
        if throttled:
            throttleds[ip] = throttleds.get(ip, 0) + 1
    
    try:
        topIPs = list(ips.items())
        topIPs.sort(reverse=True, key=secondElem)
        topIPs = topIPs[:50]

        topMethods = list(methods.items())
        topMethods.sort(reverse=True, key=secondElem)
        topMethods = topMethods[:50]

        topCacheds = list(cacheds.items())
        topCacheds.sort(reverse=True, key=secondElem)
        topCacheds = topCacheds[:50]

        topThrottleds = list(throttleds.items())
        topThrottleds.sort(reverse=True, key=secondElem)
        topThrottleds = topThrottleds[:50]
    except e:
        print("ERROR: ", e)

    result = json.dumps({"throttleEnabled": THROTTLE_ENABLED,  "req limit":USAGE_LIMIT, "rate limit": str(USAGE_LIMIT*1000/USAGE_LIMIT_WINDOW)+' req/s', "limit window": str(USAGE_LIMIT_WINDOW/1000)+' sec', "topIPs":dict(topIPs), "topMethods":dict(topMethods), "topCachedMethods": dict(topCacheds), "topThrottledIPs": dict(topThrottleds), "usage":usage})
    return web.Response(text=result,  headers=res_headers)

usage = {}
reqs = []
THROTTLE_ENABLED = False
USAGE_LIMIT = 0
USAGE_LIMIT_WINDOW = 0

bestBlock = None
cache = LRU(1024)

def getWeight(method):
    if method == 'eth_call' or method == 'eth_getLogs':
        return 2
    return 1

def refreshReqs():
    while len(reqs)>0:
        now_s = int( time.time_ns() / 1e6)
        if reqs[0][0] < now_s - USAGE_LIMIT_WINDOW:
            d = reqs.pop(0)
            # ts = d[0]
            reqIp = d[1]
            reqMethod = d[2]
            reqCached = d[3]
            reqThrottled = d[4]
            reqWeight = getWeight(reqMethod)
            if reqIp in usage:
                # minus usage if req is not cached/throttled
                if not reqCached and not reqThrottled:
                    usage[reqIp] -= reqWeight
                if usage[reqIp] <= 0:
                    del usage[reqIp]
        else:
            break


def updateUsage(ip, method, cached, throttled):
    ts = int( time.time_ns() / 1e6 )
    global reqs
    global usage
    reqs.append((ts, ip, method, cached, throttled))

    # add usage if req is not cached/throttled
    if not cached and not throttled:
        weight = getWeight(method)
        usage[ip] = usage.get(ip, 0) + weight

    refreshReqs()

def isThrottled(ip):
    if ip not in usage:
        usage[ip] = 0
        return 0
    if USAGE_LIMIT > 0 and usage[ip] > USAGE_LIMIT:
        return True # over rate limit
    return False # normal

async def update_best_block(enforce=False):
    global bestBlock
    try:
        res = await async_dispatch('{"jsonrpc":"2.0","method":"eth_getBlockByNumber","params":["latest", false],"id":1}')
        if res:
            jsonRes = json.loads(res)
            if not bestBlock or bestBlock['number'] != jsonRes['result']['number']:
                bestBlock = jsonRes['result']
                if enforce:
                    logger.info("best block enforcefully updated to %d %s", int(bestBlock['number'], 16), bestBlock['hash'])
                else:
                    logger.info("best block updated to %d %s", int(bestBlock['number'], 16), bestBlock['hash'])
    except Error as e:
        print("Error happened during best block update: ", e)
    except Exception as e:
        print("Exception happened during best block update: ", e)

async def run_best_block_updater():
    while True:
        try:
            await update_best_block()
            await asyncio.sleep(1)
        except Exception as e:
            logger.error('error happened in best_block_updater: %s', e)
            logger.error('retry in 5 seconds')
            await asyncio.sleep(5)
            logger.info("after sleep")



async def handleTextRequest(reqText, protocol, remoteIP):
    try:
        global cache
        stateRoot = ''
        if bestBlock:
            stateRoot = bestBlock['stateRoot']
        jreq = json.loads(reqText)
        first = jreq[0] if isinstance(jreq, list) else jreq

        id = first.get('id', -1)
        method = str(first.get('method', 'unknown'))
        params = str(first.get('params', 'unknown'))

        # search cache for existing answer except for these two calls:
        # eth_getBlockByNumber & eth_blockNumber
        cachekey = stateRoot + method + params
        skipCacheMatching = method == 'eth_getBlockByNumber'  or method == 'eth_blockNumber' or method == 'eth_getCode' or method == 'eth_call'
        if isinstance(jreq, list):
            for r in jreq[1:]:
                method = str(r.get('method', 'unknown'))
                params = str(r.get('params', 'unknown'))
                cachekey += method + params
                skipCacheMatching = skipCacheMatching or method == 'eth_getBlockByNumber'  or method == 'eth_blockNumber'
        
        enforceUpdateBestBlock = method =='eth_getLogs' or method == 'eth_call'
        if enforceUpdateBestBlock:
            await update_best_block(enforce=True)


        if not skipCacheMatching and cache.has_key(cachekey):
            logger.info("%s Req #%s from %s[%d/%d] served in cache: %s", protocol, str(id), remoteIP, usage.get(remoteIP,0), USAGE_LIMIT, reqText)
            cached = cache[cachekey]
            cachedRes = json.loads(cached)
            if (isinstance(cachedRes, list)):
                if len(jreq) == len(cachedRes):
                    for index, r in enumerate(cachedRes):
                        r['id']= jreq[index].get('id', id)
            else:
                cachedRes['id']= id
            updateUsage(remoteIP, method, True, False)
            return json.dumps(cachedRes)

        throttled = isThrottled(remoteIP)

        updateUsage(remoteIP, method, False, THROTTLE_ENABLED and throttled)

        throttled = isThrottled(remoteIP)

        if THROTTLE_ENABLED:
            if throttled:
                logger.info("%s Req #%s [limited(%d/%d)] from %s: %s", protocol, str(id), usage.get(remoteIP,0), USAGE_LIMIT, remoteIP, reqText)
                return json.dumps({"jsonrpc": "2.0", "error": "slow down, you're over the rate limit (%d/%d)" % (usage.get(remoteIP,0), USAGE_LIMIT), "id": id})
 
            logger.info("%s Req #%s from %s[%d/%d]: %s", protocol, str(id), remoteIP, usage.get(remoteIP,0), USAGE_LIMIT, reqText)
        else:
            logger.info("%s Req #%s from %s: %s", protocol, str(id), remoteIP, reqText)

        if "skipCache" in jreq:
            del jreq['skipCache']
        if "lang" in jreq:
            del jreq['lang']
        res = await async_dispatch(json.dumps(jreq))
        if method in ['eth_call', 'eth_getBlockByNumber', 'eth_getBlockByHash', 'eth_getTransactionByHash', 'eth_getTransactionByBlockNumberAndIndex', 'eth_getTransactionByBlockHashAndIndex']:
            logger.info("%s Res #%s: %s", protocol, str(id), res)
        else:
            logger.info("%s Res #%s: %s", protocol, str(id), res)
        cache[cachekey] = res
        return res
    except JSONDecodeError as e:
        print(e)
        return None
    except Error as e:
        print(e)
    except Exception as e:
        print(e)



async def http_handler(request):
    req = await request.text()
    remoteIP = request.remote
    if 'X-Forwarded-For' in request.headers:
        remoteIP = request.headers['X-Forwarded-For'] 
    print(request.headers)
    res = await handleTextRequest(req, 'HTTP', remoteIP)
    if res is not None:
        return web.Response(text=res, content_type="application/json", headers=res_headers)


async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    key = request.headers.get("sec-websocket-key", "")
    async for msg in ws:
        # logger.info('ws req: %s', msg)
        if msg.type == aiohttp.WSMsgType.ERROR:
            logger.error('ws connection closed with exception %s',
                         ws.exception())
            await ws.close(code=ws.close_code, message=msg.extra)
            if key in newHeadListeners:
                del newHeadListeners[key]

        elif msg.type == aiohttp.WSMsgType.TEXT and msg.data.strip():
            if msg.data == 'close':
                logger.error('close message received, close right now')
                await ws.close()

            try:
                jreq = json.loads(msg.data)
                if 'method' not in jreq or 'id' not in jreq:
                    # not a valid json request
                    continue

                id = jreq['id']
                method = jreq['method']
                params = jreq.get('params', [])
            except TypeError as e:
                logger.warning(
                    'ws: json decode error but ignored for input: %s', msg.data)
                continue
            except Exception as e:
                logger.warning(
                    'ws: got message %s but cant handle due to:', msg.data)
                logger.error(e)
                continue

            if method == "eth_subscribe":
                # handle subscribe
                if not isinstance(jreq['params'], list):
                    logger.error("params is not list, not supported")
                    continue

                subid = SUB_ID
                if params[0] == 'newHeads':
                    newHeadListeners[key] = ws
                    subid = genID(key)
                    logger.info("SUBSCRIBE to newHead: %s", key)

                elif params[0] == 'logs':
                    digest = hash_digest(str(params[1:]))
                    newkey = key+'-'+digest
                    subid = genID(newkey)
                    filters = params[1:]
                    # TODO: filter out invalid filters
                    if not isinstance(filters, list):
                        filters = [filters]

                    logListeners[newkey] = {"ws": ws, "filters": filters}
                    logger.info("SUBSCRIBE to logs: %s, filter: %s",
                                newkey, filters)
                elif params[0] == 'newPendingTransactions':
                    # FIXME: support this
                    pass
                elif params[0] == 'syncing':
                    # FIXME: support this
                    pass
                else:
                    logger.error("not supported subscription: %s", params[0])

                await ws.send_str(json.dumps({"jsonrpc": "2.0", "result": subid, "id": id}))

            elif (method == "eth_unsubscribe"):
                # handle unsubscribe
                await ws.send_str(json.dumps({"jsonrpc": "2.0", "result": True, "id": id}))
                subid = params[0]
                if key in newHeadListeners:
                    del newHeadListeners[key]
                for key in logListeners:
                    if genID(key) == subid:
                        del logListeners[key]
                logger.info("UNSUBSCRIBE: %s", key)
                await ws.close()
            elif (method == 'eth_getTransactionReceipt'):
                # handle tx receipt
                res = await handleTextRequest(msg.data, 'WS', request.remote)
                if res and json.loads(res)['result']:
                    logger.info("forward response to ws %s", key)
                    await ws.send_str(res)
                else:
                    req = json.loads(msg.data)
                    txHash = req['params'][0]
                    id = req['id']
                    receiptListeners[txHash] = {"ws":ws, "connID":key, "id":id}
                    logger.info("WAITING FOR RECEIPT: %s, conn:%s, id:%s", txHash, key, id)
            else:
                # handle normal requests
                res = await handleTextRequest(msg.data, 'WS', request.remote)
                logger.info("forward response to ws %s", key)
                await ws.send_str(res)

        elif msg.type == aiohttp.WSMsgType.BINARY:
            await ws.send_str(msg.data)

        else:
            logger.warning("Unknown REQ: %s", msg)

    print('websocket connection closed: ', key)
    return ws


async def run_server(host, port, endpoint, keystore, passcode, log, debug):
    try:
        response = requests.options(endpoint)
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        logger.error("Unable to connect to Meter-Restful server.")
        return
    meter.set_endpoint(endpoint)
    if keystore == "":
        meter.set_accounts(solo())
    else:
        meter.set_accounts(_keystore(keystore, passcode))

    http_app = web.Application()
    http_app.router.add_post("/", http_handler)
    http_app.router.add_get("/", lambda r: web.Response(headers=res_headers))
    http_app.router.add_options(
        "/", lambda r: web.Response(headers=res_headers))
    http_app.router.add_get("/health", lambda r: checkHealth())
    http_app.router.add_get("/usage", lambda r: getUsage())
    http_app.router.add_get("/reqs", lambda r: getReqs())

    ws_app = web.Application()
    ws_app.router.add_get('/', websocket_handler)
    ws_app.router.add_options("/", lambda r: web.Response(headers=res_headers))
    ws_app.router.add_get(
        "/health", lambda r: web.Response(headers=res_headers, body="OK", content_type="text/plain"))

    http_runner = web.AppRunner(http_app)
    ws_runner = web.AppRunner(ws_app)
    await http_runner.setup()
    await ws_runner.setup()
    http = web.TCPSite(http_runner, host, int(port))
    ws = web.TCPSite(ws_runner, host, int(port)+1)
    await http.start()
    logger.info("HTTP server started: http://%s:%s", host, port)
    await ws.start()
    logger.info("Websocket server started: ws://%s:%s", host, int(port)+1)

    asyncio.create_task(run_tx_observer())
    asyncio.create_task(run_new_head_observer(endpoint))
    asyncio.create_task(run_event_observer(endpoint))
    # asyncio.create_task(housekeeping())
    asyncio.create_task(run_best_block_updater())
    await asyncio.Event().wait()
    logger.info("after the loop")


@click.command()
@click.version_option(__version__)
@click.option( "--host", default="0.0.0.0")
@click.option( "--port", default=8545, type=int)
@click.option( "--endpoint", default="http://127.0.0.1:8669")
@click.option( "--keystore", default="")
@click.option( "--passcode", default="")
@click.option( "--log", default=False, type=bool)
@click.option( "--debug", default=False, type=bool)
@click.option( "--throttled", default=False, type=bool)
@click.option( "--ratelimit", default=10, type=int) # request limit in window
@click.option( "--limitwindow", default=10*60, type=int) # window in seconds
def main(host, port, endpoint, keystore, passcode, log, debug, throttled, ratelimit, limitwindow):
    global USAGE_LIMIT
    global THROTTLE_ENABLED
    global USAGE_LIMIT_WINDOW
    THROTTLE_ENABLED = throttled
    if limitwindow > 0:
        USAGE_LIMIT_WINDOW = limitwindow * 1000
    if ratelimit > 0:
        USAGE_LIMIT = ratelimit * limitwindow
    asyncio.run(run_server(host, port, endpoint, keystore,
                passcode, log, debug))


if __name__ == '__main__':
    main()
