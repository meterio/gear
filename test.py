import aiohttp
import asyncio


async def main():
    headers = {
        "accept": "application/json",
        "Connection": "keep-alive",
        "Content-Type": "application/json"
    }
    kwargs = {
        "headers": headers,
        'timeout': 5,
        'chunked': True,
        'data': None,
        'params': None,
    }

    async with aiohttp.ClientSession() as session:
        async with session.get('http://c02.meter.io:8669/blocks/11007996', **kwargs) as response:

            print("Status:", response.status)
            print("Content-type:", response.headers['content-type'])

            result = await response.json()
            print("JSON response:", result)
            print("BLOCK: ", result['number'])
            print("TXS: ", result['committee'])

loop = asyncio.get_event_loop()
loop.run_until_complete(main())
