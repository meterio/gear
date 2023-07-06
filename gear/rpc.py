import itertools
import sys
import random
import time

from .meter.client import meter
from .utils.compat import noop
from .utils.types import (
    encode_number,
    normalize_block_identifier,
    normalize_number
)
from jsonrpcserver import method, Success, Error

#
# formatter
#
TXN_FORMATTERS = {
    'value': normalize_number,
    'gas': normalize_number,
}


def input_transaction_formatter(transaction):
    return {
        k: TXN_FORMATTERS.get(k, noop)(v)
        for k, v in transaction.items()
    }


def input_log_filter_formatter(filter_params):
    params_range = {"unit": "block"}
    from_blk = filter_params.get("fromBlock", None)
    if from_blk:
        if from_blk.startswith('0x'):
            params_range["from"] = int(from_blk, 16)
        else:
            params_range['from'] = int(from_blk)
    
    # params_range["from"] = int(filter_params["fromBlock"], 16)
    to_blk = filter_params.get("toBlock", None)
    if to_blk:
        if to_blk.startswith('0x'):
            params_range["to"] = int(to_blk, 16)
        else:
            params_range['to'] = int(to_blk)
    return {
        "range": params_range,
        "topicSets": topics_formatter(filter_params.get("topics", []))
    }


def topics_formatter(eth_topics):
    if eth_topics:
        matrix = [x if isinstance(x, list) else [x] for x in eth_topics]
        return [
            {
                "topic{}".format(index): topic
                for index, topic in enumerate(e)
            }
            for e in itertools.product(*matrix)
        ]
    return []


#
#
#
@method
async def rpc_modules():
    return Success({
        "eth": "1.0",
        "net": "1.0",
        "web3": "1.0",
    })


#
# debug
#
@method
async def debug_traceTransaction(tx_hash, params):
    return Success(await meter.trace_transaction(tx_hash))


@method
async def debug_storageRangeAt(blk_hash, tx_index, contract_addr, key_start, max_result):
    res = await meter.storage_range_at(blk_hash, tx_index, contract_addr, key_start, max_result)
    return Success(res)


#
# net_api
#
@method
async def net_version():
    chainID = await meter.get_chain_id()
    return Success(str(chainID))


@method
async def net_listening():
    return Success(False)

#
# evm_api
# 没有真实实现, 只是为了实现接口
#
@method
async def evm_snapshot():
    return Success(encode_number(0))


@method
async def evm_revert(snapshot_idx=None):
    return Success(True)


#
# web3
#
def make_version():
    from . import __version__
    return "Meter-Gear/" + __version__ + "/{platform}/python{v.major}.{v.minor}.{v.micro}".format(
        v=sys.version_info,
        platform=sys.platform,
    )


@method
async def web3_clientVersion():
    return Success(make_version())


#
# eth_api
#
@method
async def eth_getStorageAt(address, position, block_identifier="best"):
    if position.startswith("0x"):
        position = position[2:]
    position = "0x{}".format(position.zfill(64))
    res = await meter.get_storage_at(
        address, position, normalize_block_identifier(block_identifier))
    return Success(res)


@method
async def eth_chainId():
    chainID = await meter.get_chain_id()
    return Success(hex(int(chainID)))


@method
async def eth_gasPrice():
    baseFee = await meter.get_base_fee()
    return Success(hex(int(baseFee)))

@method
async def eth_maxPriorityFeePerGas():
    return Success(encode_number(0))


@method
async def eth_getTransactionCount(address, block_identifier="best"):
    '''
    ethereum 用来处理 nonce, Meter 不需要
    '''
    # return encode_number(0)
    random.seed(time.time())
    nonce = random.randint(1, 0xffffffff)
    res = encode_number(nonce)
    return Success(res)


@method
async def eth_accounts():
    accounts = meter.get_accounts()
    # return Success(accounts)
    return Success([])


@method
async def eth_getCode(address, block_identifier="best"):
    res = await meter.get_code(address, normalize_block_identifier(block_identifier))
    return Success(res)


@method
async def eth_blockNumber():
    res = encode_number(await meter.get_block_number())
    return Success(res)

@method
async def eth_syncing():
    res = await meter.get_syncing()
    return Success(res)

@method
async def eth_estimateGas(transaction):
    formatted_transaction = input_transaction_formatter(transaction)
    result = await meter.estimate_gas(formatted_transaction)
    res = encode_number(result)
    return Success(res)


@method
async def eth_call(transaction, block_identifier="best"):
    formatted_transaction = input_transaction_formatter(transaction)
    res = await meter.call(formatted_transaction, normalize_block_identifier(block_identifier))
    return Success(res)


@method
async def eth_sendTransaction(transaction):
    '''
    发送未签名的交易
    '''
    formatted_transaction = input_transaction_formatter(transaction)
    res = await meter.send_transaction(formatted_transaction)
    return Success(res)


@method
async def eth_sign(transaction):
    '''
    发送未签名的交易
    '''
    return Success({})


@method
async def eth_getBlockTransactionCountByNumber(block_number):
    '''
    returns
    result - The number of transactions in a specific block represented in hexadecimal format
    '''
    res = await get_block_tx_count(block_number)
    return Success(res)

@method
async def eth_sendRawTransaction(raw):
    '''
    发送已签名的交易
    '''
    res = await meter.send_raw_transaction(raw)
    if 'error' in res and 'code' in res:
        return Error(int(res['code']), res['error'])
    return Success(res['id'])


@method
async def eth_getBalance(address, block_identifier="best"):
    res = await meter.get_balance(address, normalize_block_identifier(block_identifier))
    return Success(res)


@method
async def eth_getTransactionByHash(tx_hash):
    if tx_hash:
        res = await meter.get_transaction_by_hash(tx_hash)
        return Success(res)
    return Success(None)

@method
async def eth_getTransactionByBlockNumberAndIndex(blockNum, index):
    if blockNum:
        formattedIndex = 0
        try:
            formattedIndex = int(index)
        except:
            formattedIndex = int(index, 16)
        res = await meter.get_block(blockNum)
        if "transactions" in res:
            if len(res['transactions']) > formattedIndex:
                txHash = res['transactions'][formattedIndex]
                res = await meter.get_transaction_by_hash(txHash)
                return Success(res)
            else:
                return Error(100, "index out of bound")
        else:
            return Error(200, "no transactions found in block")
    else:
        return Error(300, "block number is required")
        
@method
async def eth_getTransactionByBlockHashAndIndex(blockHash, index):
    if blockHash:
        res = await meter.get_block(blockHash)
        if "transactions" in res:
            if len(res['transactions']) > int(index):
                txHash = res['transactions'][index]
                res = await meter.get_transaction_by_hash(txHash)
                return Success(res)
            else:
                return Error(100, "index out of bound")
        else:
            return Error(200, "no transactions found in block")
    else:
        return Error(300, "block hash is required")
 

@method
async def eth_getTransactionReceipt(tx_hash):
    if tx_hash:
        res = await meter.get_transaction_receipt(tx_hash)
        return Success(res)
    return Success(None)


@method
async def eth_getBlockByHash(block_hash, full_tx=False):
    res = await get_block(block_hash, full_tx)
    return Success(res)


@method
async def eth_getBlockByNumber(block_number, full_tx=False):
    res = await get_block(block_number, full_tx)
    return Success(res)


async def get_block(block_identifier, full_tx):
    blk = await meter.get_block(normalize_block_identifier(block_identifier))
    if blk and full_tx:
        blk["transactions"] = [await meter.get_transaction_by_hash(
            tx_hash) for tx_hash in blk["transactions"]]
    if blk:
        if 'baseFeePerGas' not in blk:
            blk['baseFeePerGas'] = '0x746a528800'
        else:
            blk['baseFeePerGas'] = hex(int(str(blk['baseFeePerGas'])))
    return blk

async def get_block_tx_count(block_identifier):
    blk = await meter.get_block(normalize_block_identifier(block_identifier))
    return len(blk['transactions']) 


@method
async def eth_newBlockFilter():
    res = await meter.new_block_filter()
    return Success(res)


@method
async def eth_uninstallFilter(filter_id):
    res = meter.uninstall_filter(filter_id)
    return Success(res)


@method
async def eth_getFilterChanges(filter_id):
    res = await meter.get_filter_changes(filter_id)
    return Success(res)


@method
async def eth_getLogs(filter_obj):
    to_blk = filter_obj.get('toBlock', None)
    latest = await meter.get_block('best')
    if (to_blk == 'latest'):
        filter_obj['toBlock'] = latest['number']
    from_blk = filter_obj.get('fromBlock', None)
    if (from_blk == 'latest'):
        filter_obj['fromBlock'] = latest['number']

    if 'toBlock' in filter_obj:
        filter_obj['toBlock'] = str(filter_obj['toBlock'])
    if 'fromBlock' in filter_obj:
        filter_obj['fromBlock'] = str(filter_obj['fromBlock'])

    result = await meter.get_logs(filter_obj.get("address", None), input_log_filter_formatter(filter_obj))
    print("RESULT:", result)
    return Success(result)

@method
async def trace_filter(filter_obj):
    to_blk = filter_obj.get('toBlock', None)
    if (to_blk == 'latest'):
        latest = await meter.get_block('best')
        filter_obj['toBlock'] = latest['number']
    res = await meter.get_trace_filter(filter_obj)
    return Success(res)

@method
async def trace_transaction(filter_obj):
    res = await meter.get_trace_transaction(filter_obj)
    return Success(res)

@method
async def trace_block(filter_obj):
    if (filter_obj and filter_obj in [ '0x23336c5', '0x23336f6','0x23336ea', '0x23336de', '0x23336cf', '0x23336f5'] ):
        return Success([])
    res = await meter.get_trace_block(filter_obj)
    return Success(res)

