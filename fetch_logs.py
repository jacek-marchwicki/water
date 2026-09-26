#!/usr/bin/env python3
"""
Fetch WaterH Collector Logs from Home Assistant over SSH.

Retrieves and optionally streams, filters, and formats logs from the
WaterH Smart Bottle Collector add-on running on a Home Assistant instance.

Supports:
  - Add-on logs (`local_waterh_collector` or `waterh_collector`)
  - Filtering by level (ERROR, WARNING, INFO, DEBUG), minimum severity, tag ([BLE], [MQTT], etc.), regex/grep, and exclude
  - Real-time log streaming/following (-f / --follow)
  - Structured JSON output (--json) for programmatic AI / script consumption
  - Standalone CLI execution or importable Python library
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
from typing import Any, Generator, Iterable

# --- Defaults ---
DEFAULT_HOST = os.environ.get("HA_HOST", "root@homeassistant.local")
FALLBACK_HOST_IP = "192.168.2.209"
DEFAULT_PORT = int(os.environ.get("HA_PORT", "22"))
DEFAULT_USER = os.environ.get("HA_USER", "root")
DEFAULT_ADDON = os.environ.get("HA_ADDON", "local_waterh_collector")
DEFAULT_SSH_KEY = os.environ.get("HA_SSH_KEY")
DEFAULT_TIMEOUT = 15.0

# Regex for parsing collector log format:
# "22:42:17 [WARNING] [BLE] Using default WRITE characteristic..."
# or "2026-09-26 22:42:17 [INFO] No specific timezone..."
LOG_PATTERN = re.compile(
    r"^(?P<timestamp>\d{2}:\d{2}:\d{2}(?:\.\d+)?|\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+"
    r"\[(?P<level>[A-Z]+)\]\s+"
    r"(?:\[(?P<tag>[^\]]+)\]\s+)?(?P<message>.*)$"
)

ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

LEVEL_ORDER = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "WARN": 30,
    "ERROR": 40,
    "CRITICAL": 50,
    "FATAL": 50,
}

# ANSI colors for terminal display
COLOR_RESET = "\033[0m"
COLOR_DIM = "\033[2m"
COLOR_BOLD = "\033[1m"
COLOR_RED = "\033[31m"
COLOR_YELLOW = "\033[33m"
COLOR_GREEN = "\033[32m"
COLOR_CYAN = "\033[36m"
COLOR_MAGENTA = "\033[35m"
COLOR_BLUE = "\033[34m"

LEVEL_COLORS = {
    "DEBUG": COLOR_DIM,
    "INFO": COLOR_GREEN,
    "WARNING": COLOR_YELLOW,
    "WARN": COLOR_YELLOW,
    "ERROR": COLOR_RED,
    "CRITICAL": COLOR_RED + COLOR_BOLD,
    "FATAL": COLOR_RED + COLOR_BOLD,
}


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from string."""
    return ANSI_ESCAPE.sub("", text)


def parse_target(host_str: str, default_user: str = "root") -> tuple[str, str]:
    """Parse user and host from a connection string like 'user@hostname' or 'hostname'."""
    clean = host_str.strip()
    if "@" in clean:
        user, host = clean.split("@", 1)
        return user or default_user, host
    return default_user, clean


def build_ssh_command(
    target_host: str,
    remote_cmd: str,
    *,
    port: int = 22,
    user: str = "root",
    key: str | None = None,
    timeout: float | None = 15.0,
    batch_mode: bool = True,
) -> list[str]:
    """Build the SSH argument list."""
    cmd = ["ssh"]
    if port != 22:
        cmd.extend(["-p", str(port)])
    if key:
        cmd.extend(["-i", os.path.expanduser(key)])
    if batch_mode:
        cmd.extend(["-o", "BatchMode=yes"])
    cmd.extend([
        "-o", "StrictHostKeyChecking=accept-new",
    ])
    if timeout and timeout > 0:
        cmd.extend(["-o", f"ConnectTimeout={int(timeout)}"])

    # If target_host doesn't contain '@', prepend user
    if "@" not in target_host and user:
        destination = f"{user}@{target_host}"
    else:
        destination = target_host

    cmd.append(destination)
    cmd.append(remote_cmd)
    return cmd


def build_remote_log_command(
    addon: str = DEFAULT_ADDON,
    lines: int | None = 50,
    follow: bool = False,
    boot: str | None = None,
) -> str:
    """Build the remote command string executed on Home Assistant OS."""
    flags: list[str] = []
    if follow:
        flags.append("-f")
    elif lines is not None and lines > 0:
        flags.extend(["-n", str(lines)])
    if boot:
        flags.extend(["-b", str(boot)])
    flags.append("--no-progress")

    flag_str = " ".join(flags)
    # ha apps logs is the modern HA CLI command; ha addons logs is the legacy alias fallback
    return f"ha apps logs {addon} {flag_str} 2>/dev/null || ha addons logs {addon} {flag_str}"


def parse_log_line(raw_line: str) -> dict[str, Any]:
    """
    Parse a single log line into a structured dictionary.

    Returns dict with keys:
      - timestamp: str | None
      - level: str | None
      - tag: str | None
      - message: str
      - raw: str
    """
    cleaned = strip_ansi(raw_line).rstrip("\r\n")
    match = LOG_PATTERN.match(cleaned)
    if match:
        groups = match.groupdict()
        level = (groups.get("level") or "").upper()
        return {
            "timestamp": groups.get("timestamp"),
            "level": level,
            "tag": groups.get("tag") or None,
            "message": groups.get("message") or "",
            "raw": cleaned,
        }
    return {
        "timestamp": None,
        "level": None,
        "tag": None,
        "message": cleaned,
        "raw": cleaned,
    }


def matches_filters(
    entry: dict[str, Any],
    *,
    level: str | None = None,
    min_level: str | None = None,
    tag: str | None = None,
    grep_pattern: re.Pattern | None = None,
    exclude_pattern: re.Pattern | None = None,
) -> bool:
    """Check if a parsed log entry matches all specified filtering criteria."""
    entry_level = (entry.get("level") or "").upper()
    raw = entry.get("raw", "")

    # Level filter (exact)
    if level:
        target_lvl = level.upper()
        if entry_level != target_lvl and f"[{target_lvl}]" not in raw.upper():
            return False

    # Min level filter
    if min_level:
        target_val = LEVEL_ORDER.get(min_level.upper(), 0)
        entry_val = LEVEL_ORDER.get(entry_level, 0)
        # If entry has no recognized level, allow it only if min_level is DEBUG or lower
        if entry_val < target_val and entry_val != 0:
            return False

    # Tag filter
    if tag:
        tag_target = tag.strip("[]").upper()
        entry_tag = (entry.get("tag") or "").upper()
        if entry_tag != tag_target and f"[{tag_target}]" not in raw.upper():
            return False

    # Grep filter
    if grep_pattern and not grep_pattern.search(raw):
        return False

    # Exclude filter
    if exclude_pattern and exclude_pattern.search(raw):
        return False

    return True


def format_log_entry(entry: dict[str, Any], color: bool = True) -> str:
    """Format a parsed log entry for terminal display."""
    if not color:
        return entry.get("raw", "")

    ts = entry.get("timestamp")
    lvl = entry.get("level")
    tag = entry.get("tag")
    msg = entry.get("message")
    raw = entry.get("raw", "")

    if not ts or not lvl:
        # Unstructured line (e.g. exception traceback or banner)
        return raw

    lvl_color = LEVEL_COLORS.get(lvl, "")
    tag_part = f"{COLOR_CYAN}[{tag}]{COLOR_RESET} " if tag else ""
    return f"{COLOR_DIM}{ts}{COLOR_RESET} {lvl_color}[{lvl}]{COLOR_RESET} {tag_part}{msg}"


def run_ssh_query(
    cmd: list[str],
    timeout: float | None = None,
) -> tuple[int, str, str]:
    """Execute an SSH command and capture output."""
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"SSH command timed out after {timeout} seconds."
    except FileNotFoundError:
        return 127, "", "Error: 'ssh' executable not found in PATH."
    except Exception as e:
        return -1, "", f"Unexpected error executing SSH: {e}"


def stream_ssh_logs(
    cmd: list[str],
) -> Generator[str, None, int]:
    """Stream lines continuously from an SSH command."""
    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # Line-buffered
        )

        if proc.stdout is None:
            return 1

        for line in iter(proc.stdout.readline, ""):
            yield line

        proc.stdout.close()
        return proc.wait()
    except KeyboardInterrupt:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        return 0
    except Exception as e:
        sys.stderr.write(f"Stream error: {e}\n")
        if proc:
            proc.kill()
        return 1


def fetch_waterh_logs(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    user: str = DEFAULT_USER,
    key: str | None = DEFAULT_SSH_KEY,
    addon: str = DEFAULT_ADDON,
    lines: int | None = 50,
    follow: bool = False,
    boot: str | None = None,
    level: str | None = None,
    min_level: str | None = None,
    tag: str | None = None,
    grep: str | None = None,
    exclude: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    raw: bool = False,
    verbose: bool = False,
) -> list[dict[str, Any]] | str:
    """
    Fetch and filter WaterH logs programmatically from Home Assistant over SSH.

    Parameters:
      host: SSH hostname or user@host
      port: SSH port (default 22)
      user: SSH user (default root)
      key: SSH private key path (optional)
      addon: Add-on slug (default local_waterh_collector)
      lines: Number of lines to retrieve (None or <= 0 for all)
      follow: If True, this function does not stream indefinitely; use stream_ssh_logs instead.
      boot: Boot ID (optional)
      level: Specific log level (e.g. "ERROR")
      min_level: Minimum log level (e.g. "WARNING" matches WARNING and ERROR)
      tag: Subsystem tag (e.g. "BLE", "MQTT")
      grep: Regex or substring pattern to include
      exclude: Regex or substring pattern to exclude
      timeout: Command timeout in seconds
      raw: If True, returns raw string output instead of parsed dictionaries
      verbose: If True, prints debugging info to stderr

    Returns:
      List of parsed log dictionaries or raw string.
    """
    u, h = parse_target(host, default_user=user)
    remote_cmd = build_remote_log_command(addon=addon, lines=lines, follow=follow, boot=boot)
    ssh_cmd = build_ssh_command(h, remote_cmd, port=port, user=u, key=key, timeout=timeout)

    if verbose:
        sys.stderr.write(f"Executing: {' '.join(ssh_cmd)}\n")

    code, stdout, stderr = run_ssh_query(ssh_cmd, timeout=timeout)

    # If default hostname failed to connect, try fallback IP
    if code != 0 and h in ("homeassistant.local", "homeassistant"):
        if verbose:
            sys.stderr.write(f"Initial attempt to {h} failed ({stderr.strip()}), trying fallback IP {FALLBACK_HOST_IP}...\n")
        ssh_cmd_fallback = build_ssh_command(FALLBACK_HOST_IP, remote_cmd, port=port, user=u, key=key, timeout=timeout)
        code_fb, stdout_fb, stderr_fb = run_ssh_query(ssh_cmd_fallback, timeout=timeout)
        if code_fb == 0 or len(stdout_fb) > 0:
            code, stdout, stderr = code_fb, stdout_fb, stderr_fb

    # Check if addon does not exist and try fallback slug
    if "does not exist" in stdout or "does not exist" in stderr:
        alt_addon = "waterh_collector" if addon == "local_waterh_collector" else "local_waterh_collector"
        if verbose:
            sys.stderr.write(f"Addon '{addon}' not found, attempting alternative slug '{alt_addon}'...\n")
        alt_remote_cmd = build_remote_log_command(addon=alt_addon, lines=lines, follow=follow, boot=boot)
        alt_ssh_cmd = build_ssh_command(h, alt_remote_cmd, port=port, user=u, key=key, timeout=timeout)
        alt_code, alt_stdout, alt_stderr = run_ssh_query(alt_ssh_cmd, timeout=timeout)
        if alt_code == 0 and "does not exist" not in alt_stdout:
            code, stdout, stderr = alt_code, alt_stdout, alt_stderr

    if code != 0 and not stdout:
        err_msg = stderr.strip() or f"SSH command failed with exit code {code}"
        raise RuntimeError(f"Failed to fetch logs from {host}: {err_msg}")

    if raw:
        return stdout

    grep_pat = re.compile(grep, re.IGNORECASE) if grep else None
    excl_pat = re.compile(exclude, re.IGNORECASE) if exclude else None

    results: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        # Skip supervisor deprecation notices if present
        if "The use of 'addons' is deprecated" in line:
            continue
        entry = parse_log_line(line)
        if matches_filters(
            entry,
            level=level,
            min_level=min_level,
            tag=tag,
            grep_pattern=grep_pat,
            exclude_pattern=excl_pat,
        ):
            results.append(entry)

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch WaterH collector logs from Home Assistant over SSH",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python3 fetch_logs.py
  python3 fetch_logs.py -n 100 --level ERROR
  python3 fetch_logs.py -f --tag BLE
  python3 fetch_logs.py --grep "disconnect|reconnect"
  python3 fetch_logs.py --json -n 20 > logs.json
  HA_HOST=192.168.2.209 python3 fetch_logs.py -n 30
""",
    )

    parser.add_argument(
        "-H", "--host",
        default=DEFAULT_HOST,
        help=f"Target SSH host or user@host (default: {DEFAULT_HOST}, env: HA_HOST)",
    )
    parser.add_argument(
        "-p", "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"SSH port (default: {DEFAULT_PORT}, env: HA_PORT)",
    )
    parser.add_argument(
        "-u", "--user",
        default=DEFAULT_USER,
        help=f"SSH user (default: {DEFAULT_USER}, env: HA_USER)",
    )
    parser.add_argument(
        "-i", "--key",
        default=DEFAULT_SSH_KEY,
        help="Path to SSH private key file (env: HA_SSH_KEY)",
    )
    parser.add_argument(
        "-a", "--addon",
        default=DEFAULT_ADDON,
        help=f"Home Assistant app/add-on slug (default: {DEFAULT_ADDON}, env: HA_ADDON)",
    )
    parser.add_argument(
        "-n", "--lines",
        type=str,
        default="50",
        help="Number of lines to fetch. Use 'all' or '0' for full log buffer (default: 50)",
    )
    parser.add_argument(
        "-f", "--follow",
        action="store_true",
        help="Stream logs in real-time continuously",
    )
    parser.add_argument(
        "-b", "--boot",
        help="Boot ID to fetch logs for (e.g. 0 for current, -1 for previous)",
    )
    parser.add_argument(
        "-l", "--level",
        choices=["DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL"],
        type=str.upper,
        help="Filter for exact log level",
    )
    parser.add_argument(
        "--min-level",
        choices=["DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL"],
        type=str.upper,
        help="Filter for minimum log severity (e.g. WARNING includes ERROR)",
    )
    parser.add_argument(
        "-T", "--tag",
        help="Filter by subsystem tag, e.g. BLE, MQTT, HTTP, INIT, DB",
    )
    parser.add_argument(
        "-g", "--grep",
        help="Case-insensitive regex pattern or keyword to filter lines",
    )
    parser.add_argument(
        "-e", "--exclude",
        help="Case-insensitive regex pattern or keyword to exclude lines",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output in structured JSON format (JSON array in batch mode, NDJSON in follow mode)",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print raw untouched output from remote SSH command",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color output",
    )
    parser.add_argument(
        "-o", "--output",
        help="Save logs to specified local file",
    )
    parser.add_argument(
        "-t", "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"SSH connection timeout in seconds (default: {DEFAULT_TIMEOUT}s)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print verbose diagnostic details to stderr",
    )

    args = parser.parse_args(argv)

    # Parse lines count
    lines_val: int | None = 50
    if args.lines.lower() in ("all", "0"):
        lines_val = None
    else:
        try:
            lines_val = int(args.lines)
        except ValueError:
            sys.stderr.write(f"Error: Invalid --lines argument '{args.lines}'. Must be an integer or 'all'.\n")
            return 1

    # Compile regex filters
    grep_pat = re.compile(args.grep, re.IGNORECASE) if args.grep else None
    excl_pat = re.compile(args.exclude, re.IGNORECASE) if args.exclude else None

    # Determine colorization
    use_color = (
        not args.no_color
        and not args.json
        and not args.raw
        and sys.stdout.isatty()
    )

    out_file = None
    if args.output:
        try:
            out_file = open(args.output, "w", encoding="utf-8")
        except OSError as e:
            sys.stderr.write(f"Error opening output file '{args.output}': {e}\n")
            return 1

    try:
        if args.follow:
            # Live streaming mode
            u, h = parse_target(args.host, default_user=args.user)
            remote_cmd = build_remote_log_command(
                addon=args.addon,
                lines=lines_val,
                follow=True,
                boot=args.boot,
            )
            ssh_cmd = build_ssh_command(
                h, remote_cmd,
                port=args.port,
                user=u,
                key=args.key,
                timeout=args.timeout,
                batch_mode=True,
            )

            if args.verbose:
                sys.stderr.write(f"Streaming: {' '.join(ssh_cmd)}\n")

            try:
                for line in stream_ssh_logs(ssh_cmd):
                    if not line.strip():
                        continue
                    if "The use of 'addons' is deprecated" in line:
                        continue
                    if args.raw:
                        sys.stdout.write(line)
                        if out_file:
                            out_file.write(line)
                        continue

                    entry = parse_log_line(line)
                    if matches_filters(
                        entry,
                        level=args.level,
                        min_level=args.min_level,
                        tag=args.tag,
                        grep_pattern=grep_pat,
                        exclude_pattern=excl_pat,
                    ):
                        if args.json:
                            formatted = json.dumps(entry)
                        else:
                            formatted = format_log_entry(entry, color=use_color)

                        sys.stdout.write(formatted + "\n")
                        sys.stdout.flush()
                        if out_file:
                            out_file.write(strip_ansi(formatted) + "\n")
                            out_file.flush()
            except KeyboardInterrupt:
                sys.stderr.write("\n[Log stream terminated by user]\n")
            return 0

        else:
            # Batch mode
            try:
                if args.raw:
                    raw_out = fetch_waterh_logs(
                        host=args.host,
                        port=args.port,
                        user=args.user,
                        key=args.key,
                        addon=args.addon,
                        lines=lines_val,
                        boot=args.boot,
                        timeout=args.timeout,
                        raw=True,
                        verbose=args.verbose,
                    )
                    sys.stdout.write(str(raw_out))
                    if out_file:
                        out_file.write(str(raw_out))
                    return 0

                entries = fetch_waterh_logs(
                    host=args.host,
                    port=args.port,
                    user=args.user,
                    key=args.key,
                    addon=args.addon,
                    lines=lines_val,
                    boot=args.boot,
                    level=args.level,
                    min_level=args.min_level,
                    tag=args.tag,
                    grep=args.grep,
                    exclude=args.exclude,
                    timeout=args.timeout,
                    raw=False,
                    verbose=args.verbose,
                )

                if args.json:
                    json_str = json.dumps(entries, indent=2)
                    sys.stdout.write(json_str + "\n")
                    if out_file:
                        out_file.write(json_str + "\n")
                else:
                    for entry in entries:  # type: ignore[union-attr]
                        formatted = format_log_entry(entry, color=use_color)
                        sys.stdout.write(formatted + "\n")
                        if out_file:
                            out_file.write(strip_ansi(formatted) + "\n")

                return 0
            except RuntimeError as err:
                sys.stderr.write(f"❌ {err}\n")
                return 1

    finally:
        if out_file:
            out_file.close()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(0))
    sys.exit(main())
