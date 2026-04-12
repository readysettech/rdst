from enum import Enum

from shared.query_capture_limits import (
    DB_QUERY_SIZE_WARN_THRESHOLD,
    DEFAULT_TIMEOUT,
    MAX_PROXIED_QUERIES,
    MAX_QUERY_LENGTH,
    MAX_RETRIES,
    RETRY_DELAY,
)


class DataManagerQueryType(Enum):
    UPSTREAM = "upstream"
    READYSET = "readyset"
    PROXYSQL = "proxysql"
    SYSTEM = "system"
    UNKNOWN = "unknown"


class DMSDbType(Enum):
    MySql = "mysql"
    PostgreSQL = "postgresql"


SYSTEM_COMMAND_SETS = {
    "system_info": {
        "query_type": DataManagerQueryType.SYSTEM,
        "schema": ["key", "value"],
        "dedup_key": "key",
        "sync_interval": 60000,
        "override": False,
        "commands": {
            "cpu_usage": {
                "description": "Get current CPU usage percentage",
                "query": "printf 'cpu_usage,%s' $(top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1)",
                "default_interval_ms": 30000,
                "default_query": True,
            },
            "memory_used_bytes": {
                "description": "Get current memory used in bytes",
                "query": "printf 'memory_used_bytes,%d' $(free -b | awk 'NR==2{print $3}')",
                "default_interval_ms": 30000,
            },
            "memory_total_bytes": {
                "description": "Get total memory in bytes",
                "query": "printf 'memory_total_bytes,%d' $(free -b | awk 'NR==2{print $2}')",
                "default_interval_ms": 30000,
            },
            "memory_available_bytes": {
                "description": "Get available memory in bytes",
                "query": "printf 'memory_available_bytes,%d' $(free -b | awk 'NR==2{print $7}')",
                "default_interval_ms": 30000,
            },
            "memory_usage_percent": {
                "description": "Get memory usage as percentage",
                "query": "printf 'memory_usage_percent,%.2f' $(free | awk 'NR==2{printf \"%.2f\", $3*100/$2}')",
                "default_interval_ms": 30000,
            },
            "disk_used_gb": {
                "description": "Get system disk space used in GB",
                "query": "printf 'disk_used_gb,%d' $(df -BG / | awk 'NR==2{gsub(/G/,\"\"); print $3}')",
                "default_interval_ms": 60000,
            },
            "disk_total_gb": {
                "description": "Get total system disk space in GB",
                "query": "printf 'disk_total_gb,%d' $(df -BG / | awk 'NR==2{gsub(/G/,\"\"); print $2}')",
                "default_interval_ms": 60000,
            },
            "readyset_disk_used_bytes": {
                "description": "Get Readyset disk space used in bytes",
                "query": "printf 'readyset_disk_used_bytes,%d' $(df -B1 /readyset 2>/dev/null | awk 'NR==2{print $3}' || echo 0)",
                "default_interval_ms": 60000,
            },
            "readyset_disk_total_bytes": {
                "description": "Get total Readyset disk space in bytes",
                "query": "printf 'readyset_disk_total_bytes,%d' $(df -B1 /readyset 2>/dev/null | awk 'NR==2{print $2}' || echo 0)",
                "default_interval_ms": 60000,
            },
            "readyset_free_disk_space_bytes": {
                "description": "Get Readyset free disk space in bytes",
                "query": "printf 'readyset_free_disk_space_bytes,%d' $(df -B1 /readyset 2>/dev/null | awk 'NR==2{print $4}' || echo 0)",
                "default_interval_ms": 60000,
            },
            "bytes_sent_per_minute": {
                "description": "Get network bytes sent per minute across all interfaces",
                "query": 'printf \'bytes_sent_per_minute,%d\\n\' $(UPTIME=$(cut -d. -f1 /proc/uptime); UPTIME_MIN=$((UPTIME/60)); BYTES=$(awk \'NR>2 && $1!="lo:" {gsub(":", "", $1); sum+=$10} END {print int(sum)}\' /proc/net/dev); echo $((UPTIME_MIN > 0 ? BYTES/UPTIME_MIN : 0)))',
                "default_interval_ms": 60000,
            },
            "bytes_recv_per_minute": {
                "description": "Get network bytes received per minute across all interfaces",
                "query": 'printf \'bytes_recv_per_minute,%d\\n\' $(UPTIME=$(cut -d. -f1 /proc/uptime); UPTIME_MIN=$((UPTIME/60)); BYTES=$(awk \'NR>2 && $1!="lo:" {gsub(":", "", $1); sum+=$2} END {print int(sum)}\' /proc/net/dev); echo $((UPTIME_MIN > 0 ? BYTES/UPTIME_MIN : 0)))',
                "default_interval_ms": 60000,
            },
            "load_1min": {
                "description": "Get 1-minute load average",
                "query": "printf 'load_1min,%s' $(uptime | awk -F'load average:' '{print $2}' | sed 's/^ *//' | tr ',' ' ' | awk '{print $1}')",
                "default_interval_ms": 60000,
            },
            "load_5min": {
                "description": "Get 5-minute load average",
                "query": "printf 'load_5min,%s' $(uptime | awk -F'load average:' '{print $2}' | sed 's/^ *//' | tr ',' ' ' | awk '{print $2}')",
                "default_interval_ms": 60000,
            },
            "load_15min": {
                "description": "Get 15-minute load average",
                "query": "printf 'load_15min,%s' $(uptime | awk -F'load average:' '{print $2}' | sed 's/^ *//' | tr ',' ' ' | awk '{print $3}')",
                "default_interval_ms": 60000,
            },
            "uptime_seconds": {
                "description": "Get system uptime in seconds",
                "query": "printf 'uptime_seconds,%d' $(awk '{print int($1)}' /proc/uptime)",
                "default_interval_ms": 300000,
            },
            "process_count": {
                "description": "Get total number of running processes",
                "query": "printf 'process_count,%d' $(ps aux | wc -l)",
                "default_interval_ms": 60000,
            },
            "tcp_established": {
                "description": "Get count of established TCP connections",
                "query": "printf 'tcp_established,%d' $(ss -tuln | grep ESTAB | wc -l)",
                "default_interval_ms": 60000,
            },
            "tcp_listen": {
                "description": "Get count of listening TCP connections",
                "query": "printf 'tcp_listen,%d' $(ss -tuln | grep LISTEN | wc -l)",
                "default_interval_ms": 60000,
            },
            "tcp_time_wait": {
                "description": "Get count of TCP connections in time-wait",
                "query": "printf 'tcp_time_wait,%d' $(ss -tuln | grep TIME-WAIT | wc -l)",
                "default_interval_ms": 60000,
            },
            "readyset_cpu": {
                "description": "Get Readyset CPU usage percentage",
                "query": "printf 'readyset_cpu,%s' $(ps -eo comm,%cpu | grep -i readyset | head -1 | awk '{print $2}' || echo 0)",
                "default_interval_ms": 30000,
            },
            "readyset_memory_mb": {
                "description": "Get Readyset memory usage in MB",
                "query": "printf 'readyset_memory_mb,%d' $(ps -eo comm,rss | grep -i readyset | head -1 | awk '{printf \"%.0f\", $2/1024}' || echo 0)",
                "default_interval_ms": 30000,
            },
            "mysql_cpu": {
                "description": "Get MySQL/MariaDB CPU usage percentage",
                "query": "printf 'mysql_cpu,%s' $(ps -eo comm,%cpu | grep -E '(mysql|mariadb)' | head -1 | awk '{print $2}' || echo 0)",
                "default_interval_ms": 60000,
            },
            "mysql_memory_mb": {
                "description": "Get MySQL/MariaDB memory usage in MB",
                "query": "printf 'mysql_memory_mb,%d' $(ps -eo comm,rss | grep -E '(mysql|mariadb)' | head -1 | awk '{printf \"%.0f\", $2/1024}' || echo 0)",
                "default_interval_ms": 60000,
            },
            "proxysql_cpu": {
                "description": "Get ProxySQL CPU usage percentage",
                "query": "printf 'proxysql_cpu,%s' $(ps -eo comm,%cpu | grep proxysql | head -1 | awk '{print $2}' || echo 0)",
                "default_interval_ms": 60000,
            },
            "proxysql_memory_mb": {
                "description": "Get ProxySQL memory usage in MB",
                "query": "printf 'proxysql_memory_mb,%d' $(ps -eo comm,rss | grep proxysql | head -1 | awk '{printf \"%.0f\", $2/1024}' || echo 0)",
                "default_interval_ms": 60000,
            },
        },
    },
}

# Backward-compatible alias. This shared module now owns only infrastructure
# command sets; feature-specific command definitions live with their slices.
COMMAND_SETS = SYSTEM_COMMAND_SETS
