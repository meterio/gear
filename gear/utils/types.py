import re
import codecs
import functools
from eth_utils import (
    big_endian_to_int,
    is_hex,
    encode_hex,
    decode_hex as _decode_hex,
)


def bytearray_to_bytestr(value):
    return bytes(value)


def is_numeric(value):
    return isinstance(value, int)


def is_binary(value):
    return isinstance(value, (bytes, bytearray))


def is_text(value):
    return isinstance(value, str)

def is_hex_str(value):
    return isinstance(value, str) and re.fullmatch(r"^0x[0-9a-fA-F]+$", value) is not None

def is_numeric_str(value):
    return isinstance(value, str) and re.fullmatch(r"^[0-9]+$", value) is not None

def is_string(value):
    return isinstance(value, (bytes, str, bytearray))


def is_integer(value):
    return isinstance(value, int)


def is_array(value):
    return isinstance(value, (list, tuple))


def force_text(value):
    if is_text(value):
        return value
    elif is_binary(value):
        return codecs.decode(value, "utf-8")
    else:
        raise TypeError("force_text Unsupported type: {0} of value: {1}".format(type(value), value))


def force_bytes(value):
    if is_binary(value):
        return bytes(value)
    elif is_text(value):
        return codecs.encode(value, "utf-8")
    else:
        raise TypeError("force_bytes Unsupported type: {0} of value: {1}".format(type(value), value))


def force_obj_to_text(obj, skip_unsupported=False):
    if is_string(obj):
        return force_text(obj)
    elif isinstance(obj, dict):
        return {
            k: force_obj_to_text(v, skip_unsupported) for k, v in obj.items()
        }
    elif isinstance(obj, (list, tuple)):
        return type(obj)(force_obj_to_text(v, skip_unsupported) for v in obj)
    elif not skip_unsupported:
        raise ValueError("Unsupported type: {0}".format(type(obj)))
    else:
        return obj


def force_obj_to_bytes(obj, skip_unsupported=False):
    if is_string(obj):
        return force_bytes(obj)
    elif isinstance(obj, dict):
        return {
            k: force_obj_to_bytes(v, skip_unsupported) for k, v in obj.items()
        }
    elif isinstance(obj, (list, tuple)):
        return type(obj)(force_obj_to_bytes(v, skip_unsupported) for v in obj)
    elif not skip_unsupported:
        raise ValueError("Unsupported type: {0}".format(type(obj)))
    else:
        return obj


def coerce_args_to_bytes(fn):
    @functools.wraps(fn)
    def inner(*args, **kwargs):
        bytes_args = force_obj_to_bytes(args, True)
        bytes_kwargs = force_obj_to_bytes(kwargs, True)
        return fn(*bytes_args, **bytes_kwargs)
    return inner


def coerce_return_to_bytes(fn):
    @functools.wraps(fn)
    def inner(*args, **kwargs):
        return force_obj_to_bytes(fn(*args, **kwargs), True).decode()
    return inner


@coerce_args_to_bytes
def strip_0x(value):
    if value.startswith(b'0x'):
        return value[2:]
    return value


@coerce_args_to_bytes
def add_0x(value):
    return b"0x" + strip_0x(value)


@coerce_return_to_bytes
def encode_data(data, length=None):
    '''Encode unformatted binary `data`.

    If `length` is given, the result will be padded like this: ``quantity_encoder(255, 3) ==
    '0x0000ff'``.
    '''

    def zpad(x, l):
        ''' Left zero pad value `x` at least to length `l`.

        >>> zpad('', 1)
        '\x00'
        >>> zpad('\xca\xfe', 4)
        '\x00\x00\xca\xfe'
        >>> zpad('\xff', 1)
        '\xff'
        >>> zpad('\xca\xfe', 2)
        '\xca\xfe'
        '''
        return b'\x00' * max(0, l - len(x)) + x
    return add_0x(encode_hex(zpad(data, length or 0)))


def encode_number(value, length=None):
    '''Encode interger quantity `data`.'''
    hex_str = ''
    if value is None:
        hex_str = '0x'
    elif is_hex_str(value):
        hex_str = value
    elif is_numeric(value):
        hex_str = '0x0' if value == 0 else hex(value)
    else:
        raise ValueError("encode_number Unsupported type: {0} for value: {1}".format(type(value), value))
    tail = hex_str[2:]
    if length:
        actLen = len(tail)
        for i in range(length*2-actLen):
            tail = '0'+tail
    return '0x' + tail


        

@coerce_args_to_bytes
def decode_hex(value):
    return _decode_hex(strip_0x(value))


def normalize_number(value):
    if value is None:
        return 0
    if is_numeric(value):
        return value
    elif is_numeric_str(value):
        return int(value, 10)
    elif is_hex_str(value):
        return int(value, 16)
    elif is_hex_str('0x'+value):
        return int('0x'+value, 16)
    else:
        raise ValueError("Unknown numeric encoding: {0}".format(value))


def normalize_block_identifier(block_identifier):
    if type(block_identifier) == dict:
        for k, v in block_identifier.items():
            if k == "blockHash":
                return v
            if k == "blockNumber":
                return v
        raise ValueError("Unknown block identifier encoding: {0}".format(block_identifier))

    if is_hex(block_identifier):
        return block_identifier
    if block_identifier is None or block_identifier == "earliest":
        return normalize_number(0)
    if block_identifier in ["best", "latest", "pending"]:  # eth 最新块用 latest 表示
        return "best"
    return normalize_number(block_identifier)
