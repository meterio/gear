# Meter-gear

Proxy [Meter](https://github.com/meterio/meter-pos)'s RESTful API to Eth JSON-RPC, to support Remix, Truffle and more (You should give priority to using Meter's RESTful API).

# Quick Start

## Installation

### On OS X

* Python 3.8+ support

1. Install the system-dependecies

    ```
    brew install openssl
    export CFLAGS="-I$(brew --prefix openssl)/include $CFLAGS"
    export LDFLAGS="-L$(brew --prefix openssl)/lib $LDFLAGS"
    ```

2. Installation of Meter-Gear and it's dependent Python packages via PyPI

    ```
    pip3 install meter-gear
    ```

### On Ubuntu

* Python 3.8+ support

1. Install the system-dependecies

    ```
    sudo apt-get install build-essential libssl-dev python-dev
    ```

2. Use a virtual environment to isolate your meter-gear project

    ```
    python3 -m venv meter-venv
    source meter-venv/bin/activate
    ```

3. Installation of Meter-Gear and it's dependent Python packages via PyPI

    ```
    pip3 install meter-gear
    ```

Note that you can activate and deactivate your virtual environment now

```
source meter-venv/bin/activate
deactivate
```

See https://docs.python.org/3/library/venv.html for more information

### On Windows

* Python 3.8+ support

1. Install Visual C++ Build Tools.

3. Installation of Meter-Gear and it's dependent Python packages via PyPI

    ```
    pip3 install meter-gear
    ```

## Run

Installing through pip will make the ``meter-gear`` command available on your machine (`must run meter client first.`)

```
meter-gear
```

This will run meter-gear on `127.0.0.1:8545`.

You can change its default behavior with the following parameters:

- **host**: rpc service host, eg: `--host 127.0.0.1`
- **port**: rpc service port, eg: `--port 8545`
- **endpoint**: meter restful service endpoint, eg: `--endpoint http://127.0.0.1:8669`
- **keystore**: keystore file path, eg: `--keystore /Users/(username)/keystore)`, default=meter stand-alone(solo) built-in accounts
- **passcode**: passcode of keystore, eg: `--passcode xxxxxxxx`
- **debug**: bool default=false, whether to display debug logs, eg: `--debug true`
- **log**: bool default=false, whether to display rpc logs, eg: `--log false`

### Resource limits and auto-tuning

Meter-Gear detects the CPU quota and memory limit from Linux cgroups and the
process file-descriptor limit from `RLIMIT_NOFILE`. It uses those resources to
set a safe Meter REST connection ceiling. The auto-tuner starts at 128 backend
calls (or the lower resource ceiling), then uses backend p95 latency, error
rate, saturation, and event-loop lag to increase or decrease concurrency.

The startup log includes the detected resources and every calculated limit.
Explicit request or operation limits remain fixed; unset limits follow the
backend auto-tuner.

| Variable | Default | Purpose |
| --- | ---: | --- |
| `GEAR_AUTOTUNE` | `1` | Enable container-aware sizing and runtime adaptation |
| `GEAR_BACKEND_CONNECTION_LIMIT` | `128` | Initial backend concurrency; fixed value when auto-tuning is disabled |
| `GEAR_BACKEND_CONNECTION_MIN` | `64` | Lower adaptive backend bound, capped by available resources |
| `GEAR_BACKEND_CONNECTION_MAX` | `512` | Upper adaptive/connector bound, capped by available resources |
| `GEAR_RPC_CONCURRENCY` | automatic | Concurrent HTTP requests and WebSocket messages; setting it disables automatic resizing for this limit |
| `GEAR_RPC_CONCURRENCY_MAX` | `1024` | Maximum automatically resized request limit |
| `GEAR_RPC_OPERATION_LIMIT` | automatic | Concurrent JSON-RPC operations; defaults to twice backend concurrency |
| `GEAR_RPC_OPERATION_LIMIT_MAX` | `1024` | Maximum automatically resized operation limit |
| `GEAR_RPC_PER_IP_CONCURRENCY` | `8` | Concurrent request envelopes allowed from one client IP |
| `GEAR_RPC_PER_IP_OPERATION_LIMIT` | `64` | Concurrent JSON-RPC operations allowed from one client IP |
| `GEAR_RPC_QUEUE_TIMEOUT` | `0.05` | Seconds to wait for request/operation admission |
| `GEAR_BACKEND_QUEUE_TIMEOUT` | `0.25` | Seconds a Meter REST call waits for adaptive backend capacity |
| `GEAR_MAX_BATCH_SIZE` | `50` | Maximum calls in one JSON-RPC batch |
| `GEAR_MAX_REQUEST_SIZE` | `1048576` | Maximum HTTP request body in bytes |

Each JSON-RPC batch consumes operation capacity equal to its element count. A
batch of 50 therefore consumes 50 slots instead of bypassing the limiter as
one HTTP request. Every actual Meter REST call is also bounded before it can
enter the `aiohttp` connection queue.

Auto-tuning behavior can be adjusted with these advanced settings:

| Variable | Default | Purpose |
| --- | ---: | --- |
| `GEAR_AUTOTUNE_INTERVAL` | `10` | Seconds between adaptive evaluations |
| `GEAR_AUTOTUNE_WINDOW` | `60` | Rolling backend sample window in seconds |
| `GEAR_AUTOTUNE_MIN_SAMPLES` | `20` | Minimum completed calls before changing a limit |
| `GEAR_BACKEND_TARGET_P95` | `0.5` | Healthy backend p95 latency in seconds |
| `GEAR_BACKEND_ERROR_THRESHOLD` | `0.02` | Error fraction that triggers a decrease |
| `GEAR_EVENT_LOOP_LAG_THRESHOLD` | `0.2` | Event-loop lag in seconds that triggers a decrease |
| `GEAR_BACKEND_TOTAL_TIMEOUT` | `10` | Total Meter REST timeout in seconds |
| `GEAR_BACKEND_CONNECT_TIMEOUT` | `2` | Meter REST connection timeout in seconds |
| `GEAR_BACKEND_READ_TIMEOUT` | `8` | Meter REST read timeout in seconds |
| `GEAR_BACKEND_KEEPALIVE_TIMEOUT` | `15` | Idle pooled connection lifetime in seconds |

Oversized or per-IP-limited requests return HTTP 429. Globally saturated
requests return HTTP 503 instead of creating unbounded backend work.

For Docker deployments, set the file-descriptor limit on the container rather
than changing it after startup. The included Compose configuration uses:

```yaml
ulimits:
  nofile:
    soft: 65536
    hard: 524288
```

### Use Remix

Change the Remix environment to Web3 provide.

![](http://oi64.tinypic.com/2u59gef.jpg)

### Use Truffle

* Truffle 4.0.6+ support

Modify the configuration of truffle first(`truffle.js`):

```js
module.exports = {
    networks: {
        development: {
            host: "localhost",
            port: 8545,
            network_id: "*" // Match any network id
        }
    }
};
```

Then you can use truffle's command line tool.
