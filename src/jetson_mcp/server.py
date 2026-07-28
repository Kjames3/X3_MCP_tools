import shlex
import subprocess
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("jetson-mcp")

def run_ssh_command(ip: str, command: str) -> str:
    """Helper to run a command over SSH on the Jetson robot."""
    remote_command = f"bash -lc {shlex.quote(command)}"
    ssh_cmd = [
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        f"jetson@{ip}",
        remote_command,
    ]
    try:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        return f"Error executing command: command timed out after 30 seconds\nOutput: {exc.stdout or ''}\nError: {exc.stderr or ''}"

    if result.returncode != 0:
        return f"Error executing command: {result.stderr}\nOutput: {result.stdout}"
    return result.stdout


def ros_setup_command() -> str:
    """Return the ROS 2 environment sourcing command for the Jetson robot."""
    return "source /opt/ros/*/setup.bash && "


@mcp.tool()
def check_connection(ip: str) -> str:
    """Check network reachability and SSH access to the Jetson robot."""
    ping_cmd = ["ping", "-c", "3", ip]
    ping_result = subprocess.run(ping_cmd, capture_output=True, text=True)
    if ping_result.returncode != 0:
        connection_report = f"Ping failed:\n{ping_result.stderr or ping_result.stdout}"
    else:
        connection_report = f"Ping succeeded:\n{ping_result.stdout}"

    ssh_result = run_ssh_command(ip, "echo ok")
    connection_report += f"\n---\nSSH test:\n{ssh_result}"
    return connection_report


@mcp.tool()
def check_disk_usage(ip: str, path: str = "/") -> str:
    """Inspect disk usage for the remote robot path."""
    quoted_path = shlex.quote(path)
    command = (
        f"df -h {quoted_path} && echo '---' && "
        f"if [ -e {quoted_path} ] && [ -d {quoted_path} ]; then du -sh {quoted_path}; fi"
    )
    return run_ssh_command(ip, command)


@mcp.tool()
def check_system_resources(ip: str) -> str:
    """Report remote CPU, memory, and process resource usage."""
    command = (
        "echo 'Uptime:' && uptime && echo '---' && "
        "echo 'Memory:' && free -h && echo '---' && "
        "echo 'Top processes:' && top -b -n 1 | head -n 20"
    )
    return run_ssh_command(ip, command)


@mcp.tool()
def check_gpu_status(ip: str) -> str:
    """Inspect GPU and Jetson accelerator status."""
    command = (
        "if command -v nvidia-smi >/dev/null 2>&1; then nvidia-smi; else echo 'nvidia-smi not installed'; fi && "
        "echo '---' && "
        "if command -v tegrastats >/dev/null 2>&1; then tegrastats --interval 1000 --iterations 1; else echo 'tegrastats not installed'; fi"
    )
    return run_ssh_command(ip, command)


@mcp.tool()
def check_temperature(ip: str) -> str:
    """Read remote thermal sensor values from the Jetson device."""
    command = (
        "for zone in /sys/class/thermal/thermal_zone*; do "
        "if [ -e \"$zone/temp\" ]; then echo \"$zone: $(cat \"$zone/temp\")\"; fi; "
        "done"
    )
    return run_ssh_command(ip, command)


@mcp.tool()
def check_ros_status(ip: str) -> str:
    """List ROS 2 nodes, topics, and services on the robot."""
    prefix = ros_setup_command()
    command = (
        f"{prefix}ros2 node list && echo '---' && "
        f"{prefix}ros2 topic list && echo '---' && "
        f"{prefix}ros2 service list"
    )
    return run_ssh_command(ip, command)


@mcp.tool()
def check_ros_logs(ip: str, package: str = "") -> str:
    """Inspect ROS-related logs or journal output on the robot."""
    if package:
        command = f"journalctl -u {shlex.quote(package)} -n 50 --no-pager"
    else:
        command = "journalctl -n 50 --no-pager"
    return run_ssh_command(ip, command)


@mcp.tool()
def check_service_status(ip: str, service: str) -> str:
    """Check systemd service status for a robot service."""
    command = f"systemctl status {shlex.quote(service)} --no-pager"
    return run_ssh_command(ip, command)


@mcp.tool()
def list_services(ip: str, state: str = "active") -> str:
    """List systemd services by state on the robot."""
    normalized_state = state.lower()
    if normalized_state not in {"active", "failed", "all"}:
        return "Invalid state. Use 'active', 'failed', or 'all'."

    command = (
        "systemctl list-units --type=service --all"
        if normalized_state == "all"
        else f"systemctl list-units --type=service --state={normalized_state}"
    )
    return run_ssh_command(ip, command)


@mcp.tool()
def manage_service(ip: str, service: str, action: str) -> str:
    """Restart, stop, start, enable, disable, or query status for a systemd service."""
    normalized_action = action.lower()
    valid_actions = {"start", "stop", "restart", "enable", "disable", "status"}
    if normalized_action not in valid_actions:
        return "Invalid action. Use start, stop, restart, enable, disable, or status."

    command = f"systemctl {normalized_action} {shlex.quote(service)}"
    return run_ssh_command(ip, command)


@mcp.tool()
def check_ros_node(ip: str, node_name: str = "") -> str:
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
def check_rosbag_info(ip: str, bag_path: str) -> str:
    """Show ROS 2 bag information for a recorded bag file."""
    command = f"{ros_setup_command()}ros2 bag info {shlex.quote(bag_path)}"
    return run_ssh_command(ip, command)


@mcp.tool()
def check_rosbag_topics(ip: str, bag_path: str) -> str:
    """List topics contained in a ROS 2 bag file."""
    command = (
        f"{ros_setup_command()}ros2 bag info {shlex.quote(bag_path)} "
        "| sed -n '/^topics:/,$p'"
    )
    return run_ssh_command(ip, command)


@mcp.tool()
def check_network(ip: str) -> str:
    """Inspect remote network interfaces, routes, and DNS configuration."""
    command = (
        "ip a && echo '---' && ip route && echo '---' && cat /etc/resolv.conf"
    )
    return run_ssh_command(ip, command)


@mcp.tool()
def check_usb_devices(ip: str) -> str:
    """List attached USB devices and camera hardware on the robot."""
    command = (
        "lsusb && echo '---' && "
        "if command -v v4l2-ctl >/dev/null 2>&1; then v4l2-ctl --list-devices; else echo 'v4l2-ctl not installed'; fi"
    )
    return run_ssh_command(ip, command)


@mcp.tool()
def check_uptime_and_boot(ip: str) -> str:
    """Inspect uptime, recent reboots, and kernel log messages."""
    command = (
        "uptime && echo '---' && "
        "last reboot | head -n 5 && echo '---' && "
        "dmesg | tail -n 50"
    )
    return run_ssh_command(ip, command)


@mcp.tool()
def sync_code(ip: str, local_path: str, remote_path: str) -> str:
    """Sync code from the local path to the robot's remote path using rsync."""
    rsync_cmd = [
        "rsync",
        "-avz",
        "--delete",
        "-e",
        "ssh -o StrictHostKeyChecking=no",
        local_path,
        f"jetson@{ip}:{remote_path}",
    ]
    result = subprocess.run(rsync_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return f"Sync failed: {result.stderr}\nOutput: {result.stdout}"
    return f"Sync successful:\n{result.stdout}"


@mcp.tool()
def view_ros_topic(ip: str, topic_name: str) -> str:
    """View data from a ROS 2 topic directly on the robot."""
    command = f"{ros_setup_command()}ros2 topic echo {shlex.quote(topic_name)} --once"
    return run_ssh_command(ip, command)


@mcp.tool()
def diagnose_errors(ip: str, target: str, is_service: bool = True) -> str:
    """Diagnose errors from a service or a script log on the robot. target is service name or log path."""
    quoted_target = shlex.quote(target)
    if is_service:
        command = f"journalctl -u {quoted_target} -n 50 --no-pager"
    else:
        command = f"tail -n 50 {quoted_target}"
    return run_ssh_command(ip, command)


@mcp.tool()
def colcon_build(ip: str, workspace_path: str, packages: str = "") -> str:
    """Run colcon build on the robot."""
    quoted_workspace = shlex.quote(workspace_path)
    package_list = [shlex.quote(package) for package in packages.split()] if packages else []
    pkg_arg = f"--packages-select {' '.join(package_list)}" if package_list else ""
    command = f"{ros_setup_command()}cd {quoted_workspace} && colcon build {pkg_arg}"
    return run_ssh_command(ip, command)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
