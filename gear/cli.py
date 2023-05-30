from jsonrpcserver import Error, async_dispatch
import json
import asyncio
import websockets
import hashlib
import aiohttp
from aiohttp import web
import time
from lru import LRU

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


async def run_new_head_observer(endpoint):
    ws_endpoint = endpoint.replace('https', 'ws').replace(
        'http', 'ws')+'/subscriptions/beat'
    while True:
        try:
            async with websockets.connect(ws_endpoint) as beatWS:
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
                            out = json.dumps({"jsonrpc": "2.0", "method": "eth_subscription", "params": {
                                             "subscription": SUB_ID, "result": blk}})
                            # logger.info('res: %s', out)
                            await ws.send_str(out)
                        except Exception as e:
                            del newHeadListeners[key]
                            logger.error(
                                'error happend for client ws %s, ignored: %s', key, e)
        except Exception as e:
            logger.error('error happened in head observer: %s', e)
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
                        try:
                            out = json.dumps({"jsonrpc": "2.0", "method": "eth_subscription", "params": {
                                             "subscription": SUB_ID, "result": result}})
                            await ws.send_str(out)
                        except Exception as e:
                            del logListeners[key]
                            logger.error(
                                'error happend: %s for client ws: %s ', e, key)
        except Exception as e:
            logger.error('error happend in event observer: %s', e)
            logger.error('log: %s', log)
            logger.error('filters: %s', filters)
            logger.error('retry in 10 seconds')
            await asyncio.sleep(10)


async def checkHealth():
    r = {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 8545}
    res = await handleTextRequest(json.dumps(r), 'Health', 'local')
    return web.Response(text=res, content_type="application/json", headers=res_headers)

async def getReqs():
    res = json.dumps(reqs)
    return web.Response(text=res, headers=res_headers)

async def getCounter():
    res = json.dumps(counter)
    return web.Response(text=res, headers=res_headers)

async def getCredits():
    res = json.dumps(credits)
    return web.Response(text=res, headers=res_headers)

counter = {}
credits = {}
reqs = []
THROTTLED = False
RATE_LIMIT_WINDOW = 5*60*1000 # 5min in millis
RATE_LIMIT = 300 # 300req/5min
CREDIT_LIMIT = 200*1 + 100*5 # 200 regular req, 100 heavy req

bestBlock = None
cache = LRU(1024)

def getCredit(method):
    if method == 'eth_call':
        return 5
    elif method == 'eth_getLogs':
        return 5
    else:
        return 1

def isThrottled(ip, method):
    ts = int( time.time_ns() / 1e6)
    global reqs
    global counter
    global credits
    if ip not in counter:
        counter[ip] = 0
    if ip not in credits:
        credits[ip] = 0
    counter[ip] += 1
    credits[ip] += getCredit(method)
    reqs.append((ts, ip, method))
    if RATE_LIMIT > 0 and counter[ip] > RATE_LIMIT:
        return 1 # over rate limit
    if CREDIT_LIMIT > 0 and credits[ip]> CREDIT_LIMIT:
        return 2 # over credit limit
    return 0 # normal

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
        await update_best_block()
        await asyncio.sleep(1)

async def housekeeping():
    global reqs
    global counter
    global credits
    while True:
        while len(reqs)>0:
            now_s = int( time.time_ns() / 1e6)
            if reqs[0][0] < now_s - RATE_LIMIT_WINDOW:
                d = reqs.pop(0)
                # ts = d[0]
                ip = d[1]
                method = d[2]
                credit = getCredit(method)
                if ip in counter:
                    counter[ip] -= 1
                    if counter[ip] <= 0:
                        del counter[ip]
                if ip in credits:
                    credits[ip] -= credit
                    if credits[ip] <= 0:
                        del credits[ip]
            else:
                break

        await asyncio.sleep(10)

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
        skipCacheMatching = method == 'eth_getBlockByNumber'  or method == 'eth_blockNumber' or method == 'eth_getCode'
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
            logger.info("%s Req #%s from %s[%d/%d] served in cache: %s", protocol, str(id), remoteIP, counter.get(remoteIP,0), credits.get(remoteIP,0), reqText)
            cached = cache[cachekey]
            cachedRes = json.loads(cached)
            if (isinstance(cachedRes, list)):
                if len(jreq) == len(cachedRes):
                    for index, r in enumerate(cachedRes):
                        r['id']= jreq[index].get('id', id)
            else:
                cachedRes['id']= id
            return json.dumps(cachedRes)


        if THROTTLED:
            result = isThrottled(remoteIP, method)
            if result == 1:
                logger.info("%s Req #%s [rate-limited(%d/%d)] from %s: %s", protocol, str(id), counter.get(remoteIP,0), RATE_LIMIT, remoteIP, reqText)
                return json.dumps({"jsonrpc": "2.0", "error": "slow down, you're over the rate limit (%d/%d)" % (counter.get(remoteIP,0), RATE_LIMIT), "id": id})
            if result == 2:
                logger.info("%s Req #%s [credit-limited(%d/%d)] from %s: %s", protocol, str(id), credits.get(remoteIP,0), CREDIT_LIMIT, remoteIP, reqText)
                return json.dumps({"jsonrpc": "2.0", "error": "slow down, you're over the rate limit (%d/%d)" % (credits.get(remoteIP,0), CREDIT_LIMIT), "id": id})
 
            logger.info("%s Req #%s from %s[%d/%d]: %s", protocol, str(id), remoteIP, counter.get(remoteIP,0), credits.get(remoteIP,0), reqText)
        else:
            logger.info("%s Req #%s from %s: %s", protocol, str(id), remoteIP, reqText)

            
        res = await async_dispatch(json.dumps(jreq))
        if method in ['eth_call', 'eth_getBlockByNumber', 'eth_getBlockByHash', 'eth_getTransactionByHash', 'eth_getTransactionByBlockNumberAndIndex', 'eth_getTransactionByBlockHashAndIndex']:
            logger.debug("%s Res #%s: %s", protocol, str(id), '[hidden]')
        else:
            logger.debug("%s Res #%s: %s", protocol, str(id), res)
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

                if params[0] == 'newHeads':
                    newHeadListeners[key] = ws
                    logger.info("SUBSCRIBE to newHead: %s", key)

                elif params[0] == 'logs':
                    digest = hash_digest(str(params[1:]))
                    newkey = key+'-'+digest
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

                await ws.send_str(json.dumps({"jsonrpc": "2.0", "result": SUB_ID, "id": id}))

            elif (method == "eth_unsubscribe"):
                # handle unsubscribe
                await ws.send_str(json.dumps({"jsonrpc": "2.0", "result": True, "id": id}))
                if key in newHeadListeners:
                    del newHeadListeners[key]
                if key in logListeners:
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
    http_app.router.add_get("/reqs", lambda r: getReqs())
    http_app.router.add_get("/counter", lambda r: getCounter())
    http_app.router.add_get("/credits", lambda r: getCredits())

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
    asyncio.create_task(housekeeping())
    asyncio.create_task(run_best_block_updater())
    await asyncio.Event().wait()


@click.command()
@click.option( "--host", default="0.0.0.0")
@click.option( "--port", default=8545, type=int)
@click.option( "--endpoint", default="http://127.0.0.1:8669")
@click.option( "--keystore", default="")
@click.option( "--passcode", default="")
@click.option( "--log", default=False, type=bool)
@click.option( "--debug", default=False, type=bool)
@click.option( "--throttled", default=False, type=bool)
@click.option( "--ratelimit", default=0, type=int)
@click.option( "--creditlimit", default=0, type=int)
def main(host, port, endpoint, keystore, passcode, log, debug, throttled, ratelimit, creditlimit):
    global CREDIT_LIMIT
    global RATE_LIMIT
    global THROTTLED
    RATE_LIMIT = ratelimit
    CREDIT_LIMIT = creditlimit
    THROTTLED = throttled
    asyncio.run(run_server(host, port, endpoint, keystore,
                passcode, log, debug))


if __name__ == '__main__':
    main()
