import math
import os
import resource
from dataclasses import dataclass


MIB = 1024 * 1024
GIB = 1024 * MIB


@dataclass(frozen=True)
class SystemResources:
    cpus: float
    memory_bytes: int
    nofile_soft: int
    nofile_hard: int


@dataclass(frozen=True)
class RuntimeLimits:
    auto_tune: bool
    resources: SystemResources
    backend_min: int
    backend_initial: int
    backend_max: int
    rpc_concurrency: int
    rpc_concurrency_max: int
    rpc_concurrency_dynamic: bool
    rpc_operation_limit: int
    rpc_operation_limit_max: int
    rpc_operation_limit_dynamic: bool
    per_ip_operation_limit: int


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return stream.read().strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def _host_cpu_count():
    try:
        return float(len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return float(os.cpu_count() or 1)


def _cgroup_cpu_limit():
    cpu_max = _read_text("/sys/fs/cgroup/cpu.max")
    if cpu_max:
        quota, period = cpu_max.split()[:2]
        if quota != "max" and float(period) > 0:
            return float(quota) / float(period)

    quota = _read_text("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
    period = _read_text("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
    if quota and period and float(quota) > 0 and float(period) > 0:
        return float(quota) / float(period)
    return None


def _host_memory_bytes():
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return 2 * GIB


def _cgroup_memory_limit():
    value = _read_text("/sys/fs/cgroup/memory.max")
    if value and value != "max":
        limit = int(value)
        if limit < (1 << 60):
            return limit

    value = _read_text("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    if value:
        limit = int(value)
        if limit < (1 << 60):
            return limit
    return None


def detect_system_resources():
    host_cpus = _host_cpu_count()
    cgroup_cpus = _cgroup_cpu_limit()
    cpus = min(host_cpus, cgroup_cpus) if cgroup_cpus else host_cpus

    host_memory = _host_memory_bytes()
    cgroup_memory = _cgroup_memory_limit()
    memory = min(host_memory, cgroup_memory) if cgroup_memory else host_memory

    nofile_soft, nofile_hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if nofile_soft == resource.RLIM_INFINITY:
        nofile_soft = 1048576
    if nofile_hard == resource.RLIM_INFINITY:
        nofile_hard = 1048576

    return SystemResources(
        cpus=max(0.1, float(cpus)),
        memory_bytes=max(128 * MIB, int(memory)),
        nofile_soft=max(32, int(nofile_soft)),
        nofile_hard=max(32, int(nofile_hard)),
    )


def _bool_value(value):
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _positive_int(environment, name, default):
    value = int(environment.get(name, default))
    if value <= 0:
        raise ValueError("{} must be positive".format(name))
    return value


def calculate_runtime_limits(resources, environment=None):
    environment = os.environ if environment is None else environment
    auto_tune = _bool_value(environment.get("GEAR_AUTOTUNE", "1"))
    backend_initial_explicit = "GEAR_BACKEND_CONNECTION_LIMIT" in environment
    backend_min_explicit = "GEAR_BACKEND_CONNECTION_MIN" in environment

    fixed_backend = _positive_int(
        environment,
        "GEAR_BACKEND_CONNECTION_LIMIT",
        128,
    )

    if auto_tune:
        configured_backend_max = _positive_int(
            environment,
            "GEAR_BACKEND_CONNECTION_MAX",
            512,
        )
        cpu_cap = max(16, int(math.floor(resources.cpus * 32)))
        memory_gib = resources.memory_bytes / float(GIB)
        memory_cap = max(16, int(math.floor(memory_gib * 16)))
        fd_reserve = min(1024, max(128, resources.nofile_soft // 8))
        fd_cap = max(16, (resources.nofile_soft - fd_reserve) // 4)
        backend_max = max(
            1,
            min(configured_backend_max, cpu_cap, memory_cap, fd_cap),
        )
        requested_min = _positive_int(
            environment,
            "GEAR_BACKEND_CONNECTION_MIN",
            64,
        )
        backend_min = min(requested_min, backend_max)
        requested_initial = min(fixed_backend, backend_max)
        if backend_initial_explicit and not backend_min_explicit:
            # An explicit initial value below the default minimum is useful
            # for small deployments and should not be silently raised.
            backend_min = min(backend_min, requested_initial)
        backend_initial = max(backend_min, requested_initial)
    else:
        backend_min = fixed_backend
        backend_initial = fixed_backend
        backend_max = fixed_backend

    derived_rpc_limit = max(1, backend_initial * 2)
    derived_rpc_max = max(derived_rpc_limit, backend_max * 2)

    rpc_concurrency_explicit = "GEAR_RPC_CONCURRENCY" in environment
    rpc_concurrency = _positive_int(
        environment,
        "GEAR_RPC_CONCURRENCY",
        derived_rpc_limit,
    )
    rpc_concurrency_max = _positive_int(
        environment,
        "GEAR_RPC_CONCURRENCY_MAX",
        max(1024, derived_rpc_max),
    )
    rpc_concurrency_max = max(rpc_concurrency, rpc_concurrency_max)

    operation_limit_explicit = "GEAR_RPC_OPERATION_LIMIT" in environment
    operation_limit = _positive_int(
        environment,
        "GEAR_RPC_OPERATION_LIMIT",
        derived_rpc_limit,
    )
    operation_limit_max = _positive_int(
        environment,
        "GEAR_RPC_OPERATION_LIMIT_MAX",
        max(1024, derived_rpc_max),
    )
    operation_limit_max = max(operation_limit, operation_limit_max)

    return RuntimeLimits(
        auto_tune=auto_tune,
        resources=resources,
        backend_min=backend_min,
        backend_initial=backend_initial,
        backend_max=backend_max,
        rpc_concurrency=rpc_concurrency,
        rpc_concurrency_max=rpc_concurrency_max,
        rpc_concurrency_dynamic=auto_tune and not rpc_concurrency_explicit,
        rpc_operation_limit=operation_limit,
        rpc_operation_limit_max=operation_limit_max,
        rpc_operation_limit_dynamic=(
            auto_tune and not operation_limit_explicit
        ),
        per_ip_operation_limit=_positive_int(
            environment,
            "GEAR_RPC_PER_IP_OPERATION_LIMIT",
            max(64, int(environment.get("GEAR_MAX_BATCH_SIZE", 50))),
        ),
    )


def load_runtime_limits(environment=None):
    return calculate_runtime_limits(
        detect_system_resources(),
        environment=environment,
    )
