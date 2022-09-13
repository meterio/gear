from email.header import decode_header
import rlp
import uuid
# import random
from gear.utils.singleton import Singleton
from gear.utils.types import (
    encode_number,
    encode_hex,
    strip_0x
)
from eth_abi import decode_abi
from gear.utils.compat import (
    meter_block_convert_to_eth_block,
    meter_receipt_convert_to_eth_receipt,
    meter_tx_convert_to_eth_tx,
    meter_log_convert_to_eth_log,
    meter_storage_convert_to_eth_storage,
    MeterTransaction,
    intrinsic_gas,
)
from .request import (
    Restful,
    get,
    post,
)
from jsonrpcserver import JsonRpcError

ERROR_SELECTOR = '0x08c379a0'
PANIC_SELECTOR = '0x4e487b71'

def _attribute(obj, key): 
    if obj:
        if key in obj:
            return obj[key]
        if 'error' in obj and 'code' in obj:
            raise JsonRpcError(int(obj['code']), obj['error'])
        raise JsonRpcError(10000, 'not valid response from node')
    return None


class MeterClient(object, metaclass=Singleton):
    def __init__(self):
        self.filter = {}

    def set_chainid(self, chainid):
        self.chainid = chainid

    def get_chainid(self):
        return self.chainid

    def set_endpoint(self, endpoint):
        restful = Restful(endpoint)
        self.eth_transactions = restful.transactions.eth
        self.transactions = restful.transactions
        self.blocks = restful.blocks
        self.accounts = restful.accounts
        self.events = restful.events
        self.debug = restful.debug

    def set_accounts(self, account_manager):
        self.account_manager = account_manager

    async def trace_transaction(self, tx_hash):
        tx = await self.transactions(tx_hash).make_request(get)
        if tx is None:
            return None
        data = {
            "name": "",
            "target": "{}/{}/0".format(tx["meta"]["blockID"], tx_hash)
        }
        return await self.debug.tracers.make_request(post, data=data)

    async def get_storage_at(self, address, position, block_identifier):
        params = {
            "revision": block_identifier
        }
        storage = await self.accounts(address).storage(
            position).make_request(get, params=params)
        return _attribute(storage, "value")

    async def storage_range_at(self, blk_hash, tx_index, contract_addr, key_start, max_result):
        data = {
            "Address": contract_addr,
            "KeyStart": key_start,
            "MaxResult": max_result,
            "target": "{}/{}/0".format(blk_hash, tx_index)
        }
        result = await self.debug("storage-range").make_request(post, data=data)
        if result is None:
            return None
        result["storage"] = meter_storage_convert_to_eth_storage(
            result["storage"])
        return result

    def get_accounts(self):
        return self.account_manager.get_accounts()

    async def get_block_number(self):
        blk = await self.blocks("best").make_request(get)
        return _attribute(blk, "number")

    async def get_syncing(self):
        blk = await self.blocks("best").make_request(get)
        num = encode_number(_attribute(blk, 'number'))
        return {"startingBlock":num, "currentBlock": num, "highestBlock": num}

    async def get_block_id(self, block_identifier):
        blk = await self.blocks(block_identifier).make_request(get)
        return _attribute(blk, "id")

    async def estimate_gas(self, transaction):
        data = {
            "data": transaction.get("data",'0x'),
            "value": encode_number(transaction.get("value", 0)),
            "caller": transaction.get("from", None),
        }
        toAddr = transaction.get("to", "0x")
        toAddr = "0x" if toAddr == None else toAddr
        result = await self.accounts(toAddr).make_request(post, data=data)
        if result is None:
            print("[WARN] empty response, estimate gas with data: ", data)
            raise JsonRpcError(2, 'no response from server', '')
        if result["reverted"]:
            print("[WARN] reverted, estimate gas with data: ", data)
            data = result.get('data', '0x')
            err = result.get('vmError', '')
            if data.startswith(ERROR_SELECTOR):
                try:
                    decoded = decode_abi(['string'], bytes.fromhex(data[10:]))
                    err += ': '+decoded[0]
                except Exception:
                    # monkey patch for undecodable error data
                    print("could not decode data, try monkey patch", data[10:])
                    raw = data[10:]
                    i=len(raw) -1
                    for i in range(len(raw)-1, 0, -2):
                        if raw[i] == '0' and raw[i-1] == '0':
                            break
                    raw = raw[:i] + '0'*len(raw[i:])
                    decoded = decode_abi(['string'], bytes.fromhex(raw))
                    err += ': '+decoded[0]

            if data.startswith(PANIC_SELECTOR):
                decoded = decode_abi(['uint256'], bytes.fromhex(data[10:]))
                err += ': '+str(decoded[0])
            raise JsonRpcError(3, err, data)
        return int(result["gasUsed"] * 1.2) + intrinsic_gas(transaction)

    async def call(self, transaction, block_identifier):
        params = {
            "revision": block_identifier,
        }
        data = {
            "data": transaction.get("data", "0x"),
            "value": encode_number(transaction.get("value", 0)),
            "caller": transaction.get("from", None),
        }
        result = await self.accounts(transaction.get("to", None)).make_request(
            post, data=data, params=params)
        if result["reverted"]:
            print("[WARN] reverted, eth_call with data: ", data)
            data = result.get('data', '0x')
            err = result.get('vmError', '')
            if data.startswith(ERROR_SELECTOR):
                try:
                    decoded = decode_abi(['string'], bytes.fromhex(data[10:]))
                    err += ': '+decoded[0]
                except Exception:
                    # monkey patch for undecodable error data
                    print("could not decode data, try monkey patch", data[10:])
                    raw = data[10:]
                    i=len(raw) -1
                    for i in range(len(raw)-1, 0, -2):
                        if raw[i] == '0' and raw[i-1] == '0':
                            break
                    raw = raw[:i] + '0'*len(raw[i:])
                    decoded = decode_abi(['string'], bytes.fromhex(raw))
                    err += ': '+decoded[0]
            if data.startswith(PANIC_SELECTOR):
                decoded = decode_abi(['uint256'], bytes.fromhex(data[10:]))
                err += ': '+str(decoded[0])
            raise JsonRpcError(3, err, data)
        return _attribute(result, 'data')

    async def send_transaction(self, transaction):
        chain_tag = int((await meter.get_block(0))["hash"][-2:], 16)
        blk_ref = int(strip_0x((await meter.get_block("best"))["hash"])[:8], 16)
        tx = MeterTransaction(chain_tag, blk_ref, transaction)
        tx.sign(self.account_manager.get_priv_by_addr(transaction["from"]))
        raw = "0x{}".format(encode_hex(rlp.encode(tx)))
        return await self.send_raw_transaction(raw)

    async def send_raw_transaction(self, raw):
        data = {
            "raw": raw
        }
        result = await self.eth_transactions.make_request(post, data=data)
        return result

    async def get_transaction_by_hash(self, tx_hash):
        tx = await self.transactions(tx_hash).make_request(get)
        return None if tx is None else meter_tx_convert_to_eth_tx(tx)

    async def get_balance(self, address, block_identifier):
        params = {
            "revision": block_identifier
        }
        accout = await self.accounts(address).make_request(get, params=params)
        return _attribute(accout, "energy")

    async def get_transaction_receipt(self, tx_hash):
        receipt = await self.transactions(tx_hash).receipt.make_request(get)
        return None if receipt is None else meter_receipt_convert_to_eth_receipt(receipt)

    async def get_block(self, block_identifier):
        blk = await self.blocks(block_identifier).make_request(get)
        return None if blk is None else meter_block_convert_to_eth_block(blk)

    async def get_transaction_count(self, address, tag):
        nonce = '0x1'
        return _attribute(nonce, "count")

    async def get_code(self, address, block_identifier):
        params = {
            "revision": block_identifier
        }
        code = await self.accounts(address).code.make_request(get, params=params)
        return _attribute(code, "code")

    async def new_block_filter(self):
        filter_id = "0x{}".format(uuid.uuid4().hex)
        current_block_num = await self.get_block_number()
        self.filter[filter_id] = BlockFilter(current_block_num, self)
        return filter_id

    def uninstall_filter(self, filter_id):
        if filter_id in self.filter:
            del self.filter[filter_id]
        return True

    async def get_filter_changes(self, filter_id):
        func = self.filter.get(filter_id)
        return await func() if func else []

    async def get_logs(self, address, query):
        result = []
        step = 10
        if isinstance(address, list) and len(address) > step:
            while len(address)>0:
                result.extend(await self.get_logs_bounded(address[:step],query))
                address = address[step:]
        else:
            result = await self.get_logs_bounded(address, query)
        return result

    async def get_logs_bounded(self, address, query):
        if address:
            if isinstance(address, list):
                query['address'] = address
            elif isinstance(address, str):
                query['address'] = [address]
        logs = await self.events.make_request(post, data=query)
        result = meter_log_convert_to_eth_log(logs)
        return result


    async def get_trace_filter(self, filter_obj):
        result = await self.debug.openeth_trace_filter.make_request(post, data=filter_obj)
        return result

    async def get_trace_transaction(self, filter_obj):
        result = await self.debug.openeth_trace_transaction.make_request(post, data=[filter_obj])
        return result

    async def get_trace_block(self, filter_obj):
        result = await self.debug.openeth_trace_block.make_request(post, data=[filter_obj])
        return result
class BlockFilter(object):

    def __init__(self, current_block_num, client):
        super(BlockFilter, self).__init__()
        self.current = current_block_num
        self.client = client

    async def __call__(self):
        result = []
        best_num = await self.client.get_block_number()
        if best_num:
            result = [
                id
                for id in [
                    await id
                    for id in map(self.client.get_block_id, range(self.current, best_num + 1))
                ]
                if id is not None
            ]
            self.current = best_num + 1
        return result


meter = MeterClient()
