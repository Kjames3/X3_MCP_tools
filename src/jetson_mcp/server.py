import os
import shlex
import subprocess
from mcp.server.fastmcp import FastMCP

from jetson_mcp.token_budget import clip_output

mcp = FastMCP("jetson-mcp")

# --- Robot configuration -----------------------------------------------------
# Every value is overridable by environment variable so this server can drive a
# different robot without code edits. The defaults describe the X3 Jetson.

SSH_USER = os.environ.get("JETSON_SSH_USER", "jetson")
# Primary hostname, then fallbacks tried in order. The X3 is reachable as "x3"
# on the LAN and via its Tailscale address when it is not; sessions historically
# lost time to `Exit 255` when only one of the two was tried.
DEFAULT_HOST = os.environ.get("JETSON_HOST", "x3")
FALLBACK_HOSTS = [
    host.strip()
    for host in os.environ.get("JETSON_FALLBACK_HOSTS", "100.64.52.55").split(",")
    if host.strip()
]
ROS_DISTRO = os.environ.get("JETSON_ROS_DISTRO", "humble")
ROS_DOMAIN_ID = os.environ.get("JETSON_ROS_DOMAIN_ID", "42")
ROS_WORKSPACE = os.environ.get("JETSON_ROS_WORKSPACE", "~/x3_ws")
# Set JETSON_SUDO_PASSWORD to let privileged tools work on a robot whose sudo
# needs a password. Leave it unset if you have installed a NOPASSWD sudoers rule;
# privileged commands then run under `sudo -n` and never touch a password.
SUDO_PASSWORD = os.environ.get("JETSON_SUDO_PASSWORD", "")

DEFAULT_TIMEOUT_S = 60

# Remembers which candidate host answered last, so we probe the fallback list
# once per server process rather than once per call.
_resolved_host: str | None = None


def _ssh_argv(host: str, remote_command: str, connect_timeout: int = 10) -> list[str]:
    return [
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        f"ConnectTimeout={connect_timeout}",
        f"{SSH_USER}@{host}",
        remote_command,
    ]


def _host_candidates(ip: str = "") -> list[str]:
    """Hosts to try, most likely first."""
    if ip:
        return [ip]
    if _resolved_host:
        return [_resolved_host] + [h for h in [DEFAULT_HOST, *FALLBACK_HOSTS] if h != _resolved_host]
    return [DEFAULT_HOST, *FALLBACK_HOSTS]


def resolve_host(ip: str = "") -> tuple[str | None, str]:
    """Return (host, report). host is None when nothing answered."""
    global _resolved_host
    attempts = []
    for host in _host_candidates(ip):
        try:
            probe = subprocess.run(
                _ssh_argv(host, "echo ok", connect_timeout=8),
                capture_output=True,
                text=True,
                timeout=20,
            )
        except subprocess.TimeoutExpired:
            attempts.append(f"{host}: timed out")
            continue
        if probe.returncode == 0:
            _resolved_host = host
            return host, f"{host}: ok"
        attempts.append(f"{host}: {(probe.stderr or '').strip() or f'exit {probe.returncode}'}")
    return None, "no host answered -- " + "; ".join(attempts)


def sudo_wrap(command: str) -> str:
    """Wrap a command so it runs with root privileges over a non-interactive SSH.

    `sudo` without a tty fails with "a terminal is required to read the
    password", so we always feed the password on stdin via `-S` and silence the
    prompt with `-p ''`. With no password configured we use `-n`, which succeeds
    only under a NOPASSWD sudoers rule and fails fast otherwise.
    """
    # The command is re-quoted into `bash -c` so that pipes, `&&`, and
    # redirections run as root too. Without this, `sudo -- a && b` would run
    # only `a` privileged and silently drop `b` back to the login user.
    payload = f"bash -c {shlex.quote(command)}"
    if SUDO_PASSWORD:
        return f"sudo -S -p '' -- {payload}"
    return f"sudo -n -- {payload}"


def _sudo_stdin() -> str | None:
    return f"{SUDO_PASSWORD}\n" if SUDO_PASSWORD else None


SYSTEMCTL = "/usr/bin/systemctl"


def systemctl_privileged(
    ip: str,
    verb: str,
    service: str,
    label: str = "systemctl",
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> str:
    """Run exactly `sudo systemctl <verb> <unit>` and nothing else.

    Deliberately does not go through sudo_wrap: that wraps commands in
    `bash -c`, which a sudoers rule scoped to systemctl cannot authorize, and
    authorizing `bash -c` would be equivalent to granting full root. Keeping
    this to a bare systemctl invocation is what lets the robot carry a tightly
    scoped NOPASSWD rule instead of a stored password.

    Anything that needs to happen around the state change (is-active, reading
    the journal) runs unprivileged as a separate command.
    """
    flag = "-S -p ''" if SUDO_PASSWORD else "-n"
    command = f"sudo {flag} -- {SYSTEMCTL} {verb} {shlex.quote(service)}"
    return run_ssh_command(
        ip,
        command,
        label=label,
        stdin=_sudo_stdin(),
        timeout_s=timeout_s,
    )


def run_ssh_command(
    ip: str = "",
    command: str = "",
    max_tokens: int = 2000,
    label: str = "ssh output",
    stdin: str | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> str:
    """Run a command over SSH on the Jetson robot.

    `ip` is optional; when empty the configured host (and its fallbacks) is used.
    `timeout_s` must cover the slowest thing the command does -- builds, sweeps,
    and benchmarks on this robot routinely need several minutes.
    """
    global _resolved_host
    remote_command = f"bash -lc {shlex.quote(command)}"
    last_error = ""

    for host in _host_candidates(ip):
        try:
            result = subprocess.run(
                _ssh_argv(host, remote_command),
                capture_output=True,
                text=True,
                input=stdin,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            return clip_output(
                f"Error executing command on {host}: timed out after {timeout_s}s. "
                f"Pass a larger timeout_s if this command is expected to be slow.\n"
                f"Output: {exc.stdout or ''}\nError: {exc.stderr or ''}",
                max_tokens=max_tokens,
                label=label,
            )

        # Exit 255 is ssh's own transport failure (host down, banner timeout) as
        # opposed to a non-zero exit from the remote command. Only that case is
        # worth retrying against another address.
        if result.returncode == 255 and not ip:
            last_error = (result.stderr or "").strip()
            continue

        _resolved_host = host
        if result.returncode != 0:
            return clip_output(
                f"Error executing command (exit {result.returncode}): {result.stderr}\nOutput: {result.stdout}",
                max_tokens=max_tokens,
                label=label,
            )
        output = result.stdout
        extra = (result.stderr or "").strip()
        if extra and (not output.strip() or len(extra) <= 2000):
            if len(extra) > 2000:
                extra = extra[:2000] + "\n[...stderr truncated...]"
            output = f"{output}\n[stderr] {extra}" if output.strip() else extra
        return clip_output(output, max_tokens=max_tokens, label=label)

    return clip_output(
        f"SSH failed against all candidate hosts ({', '.join(_host_candidates(ip))}). "
        f"Last error: {last_error}",
        max_tokens=max_tokens,
        label=label,
    )


def run_privileged_command(
    ip: str = "",
    command: str = "",
    max_tokens: int = 2000,
    label: str = "privileged output",
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> str:
    """Run a single command as root on the robot."""
    return run_ssh_command(
        ip,
        sudo_wrap(command),
        max_tokens=max_tokens,
        label=label,
        stdin=_sudo_stdin(),
        timeout_s=timeout_s,
    )


def ros_setup_command() -> str:
    """Return the ROS 2 environment preamble for the Jetson robot.

    Sourcing the distro alone is not enough here: the robot's nodes live in a
    colcon overlay and the fleet runs on a non-default ROS_DOMAIN_ID, so without
    both of those `ros2 node list` comes back empty and every ROS tool silently
    reports nothing. ROS_DISCOVERY_SERVER is cleared because a stale value in
    the login shell sends discovery to a server that is not running.
    """
    return (
        f"source /opt/ros/{ROS_DISTRO}/setup.bash && "
        f"source {ROS_WORKSPACE}/install/setup.bash 2>/dev/null; "
        f"export ROS_DOMAIN_ID={ROS_DOMAIN_ID}; "
        "unset ROS_DISCOVERY_SERVER; "
    )


@mcp.tool()
def check_connection(ip: str = "") -> str:
    """Check network reachability and SSH access to the Jetson robot.

    Reports which of the candidate addresses answered, so a robot that has moved
    between the LAN and Tailscale is diagnosed rather than just "unreachable".
    """
    host, resolution = resolve_host(ip)
    report = f"Host resolution: {resolution}"
    if host is None:
        return report

    report += f"\nUsing: {host}"
    try:
        ping_result = subprocess.run(
            ["ping", "-c", "3", "-W", "2", host],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if ping_result.returncode != 0:
            # Tailscale addresses often drop ICMP while SSH works fine, so a
            # failed ping on its own does not mean the robot is down.
            report += f"\n---\nPing failed (not conclusive):\n{ping_result.stderr or ping_result.stdout}"
        else:
            report += f"\n---\nPing succeeded:\n{ping_result.stdout}"
    except subprocess.TimeoutExpired:
        report += "\n---\nPing timed out (not conclusive)."

    report += f"\n---\nSSH test:\n{run_ssh_command(host, 'echo ok; uptime')}"
    return report


@mcp.tool()
def check_disk_usage(ip: str = "", path: str = "/") -> str:
    """Inspect disk usage for the remote robot path."""
    quoted_path = shlex.quote(path)
    command = (
        f"df -h {quoted_path} && echo '---' && "
        f"if [ -d {quoted_path} ] && [ {quoted_path} != / ]; then "
        f"du -sh {quoted_path} 2>/dev/null; fi"
    )
    return run_ssh_command(ip, command)


@mcp.tool()
def check_system_resources(ip: str = "") -> str:
    """Report remote CPU, memory, and process resource usage."""
    command = (
        "echo 'Uptime:' && uptime && echo '---' && "
        "echo 'Memory:' && free -h && echo '---' && "
        "echo 'Top processes:' && top -b -n 1 | head -n 20"
    )
    return run_ssh_command(ip, command)


@mcp.tool()
def check_gpu_status(ip: str = "") -> str:
    """Inspect GPU and Jetson accelerator status."""
    command = (
        "if command -v nvidia-smi >/dev/null 2>&1; then nvidia-smi; else echo 'nvidia-smi not installed'; fi && "
        "echo '---' && "
        "if command -v tegrastats >/dev/null 2>&1; then tegrastats --interval 1000 --iterations 1; else echo 'tegrastats not installed'; fi"
    )
    return run_ssh_command(ip, command)


@mcp.tool()
def check_temperature(ip: str = "") -> str:
    """Read remote thermal sensor values from the Jetson device."""
    command = (
        "for zone in /sys/class/thermal/thermal_zone*; do "
        "if [ -e \"$zone/temp\" ]; then echo \"$zone: $(cat \"$zone/temp\")\"; fi; "
        "done"
    )
    return run_ssh_command(ip, command)


@mcp.tool()
def check_ros_status(ip: str = "") -> str:
    """List ROS 2 nodes, topics, and services on the robot."""
    command = (
        f"{ros_setup_command()}"
        "ros2 node list && echo '---' && "
        "ros2 topic list && echo '---' && "
        "ros2 service list"
    )
    return run_ssh_command(ip, command, max_tokens=3000, label="check_ros_status")


@mcp.tool()
def check_service_status(service: str, ip: str = "") -> str:
    """Check systemd service status for a robot service."""
    # exit 3 just means "inactive"; the status text is still the answer.
    command = f"systemctl status {shlex.quote(service)} --no-pager; exit 0"
    return run_ssh_command(ip, command)


@mcp.tool()
def list_services(ip: str = "", state: str = "active") -> str:
    """List systemd services by state on the robot."""
    normalized_state = state.lower()
    if normalized_state not in {"active", "failed", "all"}:
        return "Invalid state. Use 'active', 'failed', or 'all'."

    command = (
        "systemctl list-units --type=service --all"
        if normalized_state == "all"
        else f"systemctl list-units --type=service --state={normalized_state}"
    )
    return run_ssh_command(ip, command, max_tokens=3000, label="list_services")


@mcp.tool()
def manage_service(service: str, action: str, ip: str = "") -> str:
    """Restart, stop, start, enable, disable, or query status for a systemd service."""
    normalized_action = action.lower()
    valid_actions = {"start", "stop", "restart", "enable", "disable", "status"}
    if normalized_action not in valid_actions:
        return "Invalid action. Use start, stop, restart, enable, disable, or status."

    quoted_service = shlex.quote(service)
    if normalized_action == "status":
        return run_ssh_command(
            ip,
            f"systemctl status {quoted_service} --no-pager; exit 0",
            label="manage_service",
        )

    # start/stop/restart/enable/disable all change system state and fail
    # silently-looking ("Interactive authentication required") without root.
    result = systemctl_privileged(
        ip, normalized_action, service, label="manage_service"
    )
    state = run_ssh_command(
        ip,
        f"systemctl is-active {quoted_service}; systemctl is-enabled {quoted_service} 2>&1; exit 0",
        label="manage_service.state",
    )
    return f"{normalized_action} {service}:\n{result}\n--- state ---\n{state}"


@mcp.tool()
def restart_service_privileged(service: str, ip: str = "", sudo_password: str = "") -> str:
    """Restart one systemd service with sudo, then report its active state.

    Deprecated: ``manage_service(service, "restart")`` now runs with root and
    does the same thing. Kept so existing callers keep working.

    ``sudo_password`` overrides the JETSON_SUDO_PASSWORD environment variable
    for this call only. It is sent through SSH standard input and is never
    written to a command line, log, or file.
    """
    if sudo_password:
        result = run_ssh_command(
            ip,
            f"sudo -S -p '' -- {SYSTEMCTL} restart {shlex.quote(service)}",
            label="restart_service_privileged",
            stdin=f"{sudo_password}\n",
        )
    else:
        result = systemctl_privileged(
            ip, "restart", service, label="restart_service_privileged"
        )
    state = run_ssh_command(
        ip, f"systemctl is-active {shlex.quote(service)}; exit 0", label="restart.state"
    )
    return f"{result}\n--- state ---\n{state}"


@mcp.tool()
def check_ros_node(ip: str = "", node_name: str = "") -> str:
    """Show ROS 2 node status and related runtime errors."""
    prefix = ros_setup_command()
    if not node_name:
        return run_ssh_command(ip, f"{prefix}ros2 node list")

    quoted_node = shlex.quote(node_name)
    command = (
        f"{prefix}ros2 node info {quoted_node} && echo '---' && "
        f"journalctl -n 200 --no-pager | grep -i {quoted_node} | grep -Ei 'error|warn|exception|fatal' | tail -n 50"
    )
    return run_ssh_command(ip, command)


@mcp.tool()
def check_rosbag_info(bag_path: str, ip: str = "") -> str:
    """Show ROS 2 bag information for a recorded bag file."""
    command = f"{ros_setup_command()}ros2 bag info {shlex.quote(bag_path)}"
    return run_ssh_command(ip, command)


@mcp.tool()
def check_rosbag_topics(bag_path: str, ip: str = "") -> str:
    """List topics contained in a ROS 2 bag file."""
    command = (
        f"{ros_setup_command()}ros2 bag info {shlex.quote(bag_path)} "
        "| sed -n '/^Topic information:/,$p'"
    )
    return run_ssh_command(ip, command)


@mcp.tool()
def check_network(ip: str = "") -> str:
    """Inspect remote network interfaces, routes, and DNS configuration."""
    command = (
        "ip a && echo '---' && ip route && echo '---' && cat /etc/resolv.conf"
    )
    return run_ssh_command(ip, command, max_tokens=2500, label="check_network")


@mcp.tool()
def check_usb_devices(ip: str = "") -> str:
    """List attached USB devices and camera hardware on the robot."""
    command = (
        "lsusb && echo '---' && "
        "if command -v v4l2-ctl >/dev/null 2>&1; then v4l2-ctl --list-devices; else echo 'v4l2-ctl not installed'; fi"
    )
    return run_ssh_command(ip, command)


@mcp.tool()
def check_uptime_and_boot(ip: str = "") -> str:
    """Inspect uptime, recent reboots, and kernel log messages."""
    command = (
        "uptime && echo '---' && "
        "last reboot | head -n 5 && echo '---' && "
        "echo 'Kernel errors:' && "
        "(dmesg --level=err,crit,alert,emerg 2>/dev/null || dmesg) | tail -n 20"
    )
    return run_ssh_command(ip, command, max_tokens=1500, label="check_uptime_and_boot")


def _summarize_itemize(raw: str, side: str) -> str:
    """Turn rsync --itemize-changes output into a plain drift summary.

    rsync's flag string is YXcstpoguax: position 2 is 'c' when the checksum
    differs and 's' when the size does. A file whose only difference is its
    mtime is not drift, so those are counted rather than listed.
    """
    content, only, mtime_only = [], [], 0
    for line in raw.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        flags, name = parts
        if len(flags) < 4 or flags[1] not in "fdLDS":
            continue
        if name.endswith("/"):
            continue
        if "+++++++++" in flags:
            only.append(name)
        elif flags[2] in "cs" or flags[3] in "cs":
            content.append(name)
        elif flags[3] == "t":
            mtime_only += 1

    lines = []
    if content:
        lines.append(f"Content differs ({len(content)}):")
        lines += [f"  {name}" for name in content]
    if only:
        lines.append(f"Only on the {side} ({len(only)}):")
        lines += [f"  {name}" for name in only]
    if not content and not only:
        lines.append("No content differences.")
    if mtime_only:
        lines.append(f"({mtime_only} more differ only by timestamp -- not real drift.)")
    return "\n".join(lines)


def _rsync(
    local_path: str,
    remote_path: str,
    ip: str,
    direction: str,
    dry_run: bool,
    delete: bool,
    extra: list[str],
    label: str,
    timeout_s: int = 300,
) -> str:
    host, report = resolve_host(ip)
    if host is None:
        return f"Cannot reach the robot: {report}"

    remote = f"{SSH_USER}@{host}:{remote_path}"
    if direction == "push":
        source, dest = local_path, remote
    elif direction == "pull":
        source, dest = remote, local_path
    else:
        return "Invalid direction. Use 'push' (laptop -> robot) or 'pull' (robot -> laptop)."

    rsync_cmd = ["rsync", "-avz", "-e", "ssh -o StrictHostKeyChecking=no"]
    if dry_run:
        rsync_cmd.append("--dry-run")
    if delete:
        rsync_cmd.append("--delete")
    rsync_cmd.extend(extra)
    rsync_cmd.extend([source, dest])

    try:
        result = subprocess.run(
            rsync_cmd, capture_output=True, text=True, timeout=timeout_s
        )
    except subprocess.TimeoutExpired:
        return f"rsync timed out after {timeout_s}s ({direction} {source} -> {dest})."

    header = f"{'DRY RUN -- no files changed' if dry_run else 'APPLIED'}: {direction} {source} -> {dest}\n"
    if result.returncode != 0:
        return clip_output(
            f"{header}rsync failed: {result.stderr}\nOutput: {result.stdout}",
            max_tokens=2000,
            label=label,
        )
    return clip_output(header + result.stdout, max_tokens=2500, label=label)


@mcp.tool()
def sync_code(
    local_path: str,
    remote_path: str,
    ip: str = "",
    direction: str = "push",
    dry_run: bool = True,
    delete: bool = False,
) -> str:
    """Sync code between the laptop and the robot with rsync.

    ``direction`` is "push" (laptop -> robot) or "pull" (robot -> laptop).

    Defaults are deliberately safe: ``dry_run`` is on, so the first call only
    reports what would change -- call again with ``dry_run=False`` to apply.
    ``delete`` (removing files at the destination that are absent at the source)
    is off unless you ask for it.
    """
    return _rsync(
        local_path,
        remote_path,
        ip,
        direction,
        dry_run,
        delete,
        extra=[],
        label="sync_code",
    )


@mcp.tool()
def diff_code(
    local_path: str,
    remote_path: str,
    ip: str = "",
    show_diff: str = "",
    exclude: str = "",
) -> str:
    """Compare a laptop directory against its counterpart on the robot.

    Reports which files genuinely differ and in which direction, without
    changing anything. Comparison is by checksum, not timestamp, so a file that
    was merely re-copied does not show up as drift.

    Build artifacts and editor/backup droppings are excluded by default; pass
    ``exclude`` (space-separated rsync patterns) to replace that list. Pass
    ``show_diff`` a filename relative to the two paths for a unified diff of
    that one file.

    Use this when the robot and the laptop have drifted and you need to know
    what actually differs before choosing a sync direction.
    """
    patterns = exclude.split() if exclude else [
        "__pycache__/",
        "*.pyc",
        "*.bak-*",
        ".cache/",
        "build/",
        "install/",
        "log/",
        ".git/",
    ]
    # -c compares checksums instead of size+mtime, so the result is real
    # content drift rather than every file that was ever re-copied.
    itemize = ["-c", "--itemize-changes", "--out-format=%i %n"]
    for pattern in patterns:
        itemize.extend(["--exclude", pattern])
    outbound = _rsync(
        local_path, remote_path, ip, "push", True, False, itemize, "diff_code.push"
    )
    inbound = _rsync(
        local_path, remote_path, ip, "pull", True, False, itemize, "diff_code.pull"
    )
    report = (
        "=== LAPTOP -> ROBOT (what a push would change) ===\n"
        f"{_summarize_itemize(outbound, 'laptop')}\n\n"
        "=== ROBOT -> LAPTOP (what a pull would change) ===\n"
        f"{_summarize_itemize(inbound, 'robot')}"
    )

    if show_diff:
        host, host_report = resolve_host(ip)
        if host is None:
            report += f"\n\nCannot fetch remote file: {host_report}"
        else:
            local_file = os.path.join(local_path, show_diff)
            remote_file = os.path.join(remote_path, show_diff)
            fetched = run_ssh_command(
                ip, f"cat {shlex.quote(remote_file)}", max_tokens=6000, label="remote file"
            )
            try:
                import difflib

                with open(local_file) as handle:
                    local_lines = handle.read().splitlines(keepends=True)
                unified = "".join(
                    difflib.unified_diff(
                        fetched.splitlines(keepends=True),
                        local_lines,
                        fromfile=f"robot:{remote_file}",
                        tofile=f"laptop:{local_file}",
                    )
                )
                report += f"\n\n=== diff of {show_diff} ===\n" + (
                    unified or "(identical)"
                )
            except OSError as exc:
                report += f"\n\nCannot read {local_file}: {exc}"

    return clip_output(report, max_tokens=4000, label="diff_code")


@mcp.tool()
def view_ros_topic(topic_name: str, ip: str = "") -> str:
    """View data from a ROS 2 topic directly on the robot."""
    command = f"{ros_setup_command()}ros2 topic echo {shlex.quote(topic_name)} --once"
    return run_ssh_command(ip, command)


@mcp.tool()
def diagnose_errors(target: str, ip: str = "", is_service: bool = True) -> str:
    """Diagnose errors from a service or a script log on the robot. target is service name or log path."""
    quoted_target = shlex.quote(target)
    if is_service:
        command = f"journalctl -u {quoted_target} -n 50 --no-pager"
    else:
        command = f"tail -n 50 {quoted_target}"
    return run_ssh_command(ip, command)


@mcp.tool()
def colcon_build(workspace_path: str = "", ip: str = "", packages: str = "", timeout_s: int = 900) -> str:
    """Run colcon build on the robot.

    Builds are slow; ``timeout_s`` defaults to 15 minutes rather than the
    general-purpose SSH default.
    """
    quoted_workspace = shlex.quote(workspace_path or ROS_WORKSPACE)
    package_list = [shlex.quote(package) for package in packages.split()] if packages else []
    pkg_arg = f"--packages-select {' '.join(package_list)}" if package_list else ""
    command = (
        f"cd {quoted_workspace} && "
        f"source /opt/ros/{ROS_DISTRO}/setup.bash && "
        f"colcon build --symlink-install {pkg_arg} 2>&1 | tail -n 60"
    )
    return run_ssh_command(
        ip, command, max_tokens=3000, label="colcon_build", timeout_s=timeout_s
    )


@mcp.tool()
def journal_grep(
    service: str,
    ip: str = "",
    since: str = "-15min",
    pattern: str = "",
    exclude: str = "",
    lines: int = 100,
) -> str:
    """Search a service's journal with a time window and filters.

    ``since`` is any systemd time spec ("-15min", "-2h", "today", "14:00").
    ``pattern`` and ``exclude`` are case-insensitive extended regexes; the
    robot's logs are chatty enough that ``exclude`` is usually what makes the
    result readable (for example excluding per-frame estimator or FPS lines).
    """
    parts = [
        f"journalctl -u {shlex.quote(service)} --since {shlex.quote(since)} --no-pager 2>/dev/null"
    ]
    if pattern:
        parts.append(f"grep -iE {shlex.quote(pattern)}")
    if exclude:
        parts.append(f"grep -viE {shlex.quote(exclude)}")
    parts.append(f"tail -n {int(lines)}")
    return run_ssh_command(
        ip, " | ".join(parts), max_tokens=3000, label="journal_grep"
    )


@mcp.tool()
def restart_and_wait(
    service: str,
    ready_pattern: str = "",
    ip: str = "",
    timeout_s: int = 120,
) -> str:
    """Restart a service and block until it is genuinely ready.

    ``systemctl restart`` returns as soon as the process is spawned, which for
    this robot's server is well before it has opened its socket and finished
    bringing hardware up. Supply ``ready_pattern`` (an extended regex matched
    against the new journal output, e.g. "Server started on ws") and this waits
    for that line to appear, then reports the service state either way.

    With no ``ready_pattern`` it just restarts and reports whether the unit
    went active.
    """
    quoted_service = shlex.quote(service)
    deadline = max(10, int(timeout_s) - 20)

    # The restart itself is the only part that needs root; the wait loop and the
    # journal read run as the login user so that a systemctl-scoped sudoers rule
    # is enough to authorize this tool.
    restart = systemctl_privileged(
        ip, "restart", service, label="restart_and_wait", timeout_s=min(timeout_s, 60)
    )
    if "Error executing command" in restart or "SSH failed" in restart:
        return f"Restart failed, not waiting:\n{restart}"

    window = deadline + 30
    if ready_pattern:
        wait_block = f"""
start=$(date +%s)
found=timeout
while [ $(( $(date +%s) - start )) -lt {deadline} ]; do
  if journalctl -u {quoted_service} --since "-{window}s" --no-pager 2>/dev/null \
       | grep -qE {shlex.quote(ready_pattern)}; then found=ready; break; fi
  if ! systemctl is-active --quiet {quoted_service}; then found=unit-died; break; fi
  sleep 2
done
echo "result=$found after $(( $(date +%s) - start ))s"
"""
    else:
        wait_block = 'sleep 3; echo "result=not-waited"\n'

    command = f"""{wait_block}
echo "state=$(systemctl is-active {quoted_service} || true)"
echo "--- last journal lines ---"
journalctl -u {quoted_service} --since "-{window}s" --no-pager 2>/dev/null | tail -n 25
"""
    return run_ssh_command(
        ip, command, max_tokens=2500, label="restart_and_wait", timeout_s=timeout_s
    )


@mcp.tool()
def check_robot_devices(ip: str = "", symlinks: str = "") -> str:
    """Check that the robot's serial device symlinks resolved.

    ``lsusb`` tells you a board enumerated; it does not tell you whether udev
    published the stable name the code actually opens, which is the failure that
    bites here. ``symlinks`` is a space-separated list of /dev names; it
    defaults to the X3's set.
    """
    names = symlinks.split() if symlinks else [
        "openrb150",
        "rosmaster",
        "lx16a",
    ]
    checks = " ".join(f"/dev/{shlex.quote(name)}" for name in names)
    command = (
        f"echo '=== expected symlinks ==='; ls -l {checks} 2>&1; "
        "echo; echo '=== raw serial devices ==='; ls -l /dev/ttyUSB* /dev/ttyACM* 2>&1; "
        "echo; echo '=== udev rules ==='; ls /etc/udev/rules.d/ 2>&1; "
        "echo; echo '=== dialout membership ==='; id -nG"
    )
    return run_ssh_command(
        ip, command, max_tokens=2000, label="check_robot_devices"
    )


@mcp.tool()
def check_gui_health(ip: str = "", ports: str = "8080 8081", service: str = "x3_server") -> str:
    """Diagnose why the GUI or its websocket is unreachable from a browser.

    Checks three things in the order they fail: the unit is running, it is
    actually listening on the expected ports, and the port accepts a connection
    from this laptop (which is what the browser has to do).
    """
    port_list = [port for port in ports.split() if port.isdigit()]
    if not port_list:
        return "No valid ports given."

    pattern = "|".join(port_list)
    command = (
        f"echo '=== unit ==='; systemctl is-active {shlex.quote(service)}; "
        f"echo; echo '=== listening sockets ==='; "
        f"(ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null) | grep -E ':({pattern})' || echo 'NOT LISTENING on any of: {ports}'"
    )
    report = run_ssh_command(ip, command, max_tokens=1500, label="check_gui_health")

    host, host_report = resolve_host(ip)
    report += f"\n\n=== reachable from this laptop? ({host_report}) ==="
    if host is not None:
        import socket

        for port in port_list:
            try:
                with socket.create_connection((host, int(port)), timeout=5):
                    report += f"\n{host}:{port} -> connect OK"
            except OSError as exc:
                report += f"\n{host}:{port} -> {exc}"
    return report


@mcp.tool()
def cleanup_strays(pattern: str, ip: str = "", dry_run: bool = True) -> str:
    """Find, and optionally kill, leftover processes matching a pattern.

    Orphaned benchmark or test nodes keep publishing and quietly poison the next
    measurement. ``pattern`` is a pgrep -f regex. ``dry_run`` is on by default:
    the first call only lists what matches, so call again with
    ``dry_run=False`` once you have read the list.
    """
    # Bracket the first character so the pgrep/pkill process does not match
    # its own command line.
    if pattern and pattern[0].isalnum():
        safe_pattern = f"[{pattern[0]}]{pattern[1:]}"
    else:
        safe_pattern = pattern
    quoted = shlex.quote(safe_pattern)

    if dry_run:
        command = (
            f"echo '=== would kill (dry run) ==='; pgrep -af {quoted} 2>/dev/null || echo 'no matches'"
        )
        return run_ssh_command(ip, command, max_tokens=1500, label="cleanup_strays")

    command = (
        f"echo '=== before ==='; pgrep -af {quoted} 2>/dev/null || echo 'no matches'; "
        f"pkill -f {quoted}; sleep 2; "
        f"echo '=== remaining ==='; pgrep -af {quoted} 2>/dev/null || echo 'none'"
    )
    return run_ssh_command(ip, command, max_tokens=1500, label="cleanup_strays")


def main():
    mcp.run()


if __name__ == "__main__":
    main()
