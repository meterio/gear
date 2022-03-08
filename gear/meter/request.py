import aiohttp
import json

async def post(endpoint_uri, data, **kwargs):
    async with aiohttp.ClientSession() as session:
        async with session.post(endpoint_uri, json=data, **kwargs) as response:
            # print("Raw response from server: ", response.status, await response.text())
            ctype = response.headers['Content-Type']
            expect = "text/plain"
            if expect in ctype:
                await response.text()
                return response
            else:
                await response.json()
                return response

async def get(endpoint_uri, params, **kwargs):
    async with aiohttp.ClientSession() as session:
        async with session.get(endpoint_uri, params=params, **kwargs) as response:
            # print("Raw response from server: ", response.status,  await response.text())
            ctype = response.headers['Content-Type']
            expect = "text/plain"
            if expect in ctype:
                await response.text()
                return response
            else:
                await response.json()
                return response

class Restful(object):

    def __init__(self, endpoint):
        super(Restful, self).__init__()
        self._endpoint = endpoint

    def __call__(self, parameter):
        if parameter is not None:
            return Restful('%s/%s' % (self._endpoint, parameter))
        return self

    def __getattr__(self, resource):
        return Restful('%s/%s' % (self._endpoint, resource))

    async def make_request(self, method, params=None, data=None, **kwargs):
        headers = {
            "accept": "application/json",
            "Connection": "keep-alive",
            "Content-Type": "application/json"
        }
        kwargs.setdefault('headers', headers)
        kwargs.setdefault('timeout', 10)
        kwargs.setdefault('chunked', True)
        error = None
        try:
            response = await method(self._endpoint, params=params, data=data, **kwargs)
            return await response.json()
        except aiohttp.ClientConnectionError as e:
            print("Unable to connect to Meter-Restful server:")
            error = e
            raise e
        except Exception as e:
            text = ''
            try:
                text = await response.text()
                error = Exception(text.strip('\n'))
            except:
                error = e
                raise e
        print("Meter-Restful server Err:")
        print("Error message from meter node:", error)
        #raise error
        x ='{"id": "' + text.strip('\n') + '"}' 
        #print(x)
        return json.loads(x)
