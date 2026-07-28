import shlex
import subprocess
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("jetson-mcp")

def run_ssh_command(ip: str, command: str) -> str:
    """Helper to run a command over SSH on the Jetson robot."""
    ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", f"jetson@{ip}", f"bash -c '{command}'"]
    result = subprocess.run(ssh_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return f"Error executing command: {result.stderr}\nOutput: {result.stdout}"
    return result.stdout


def ros_setup_command() -> str:
    """Return the ROS 2 environment sourcing command for the Jetson robot."""
    return "source /opt/ros/*/setup.bash && "


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
def sync_code(ip: str, local_path: str, remote_path: str) -> str:
    """Sync code from the local path to the robot's remote path using rsync."""
    rsync_cmd = [
        "rsync", "-avz", "--delete",
        "-e", "ssh -o StrictHostKeyChecking=no",
        local_path, f"jetson@{ip}:{remote_path}"
    ]
    result = subprocess.run(rsync_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return f"Sync failed: {result.stderr}\nOutput: {result.stdout}"
    return f"Sync successful:\n{result.stdout}"

@mcp.tool()
def view_ros_topic(ip: str, topic_name: str) -> str:
    """View data from a ROS 2 topic directly on the robot."""
    command = f"source /opt/ros/*/setup.bash && ros2 topic echo {topic_name} --once"
    return run_ssh_command(ip, command)

@mcp.tool()
def diagnose_errors(ip: str, target: str, is_service: bool = True) -> str:
    """Diagnose errors from a service or a script log on the robot. target is service name or log path."""
    if is_service:
        command = f"journalctl -u {target} -n 50 --no-pager"
    else:
        command = f"tail -n 50 {target}"
    return run_ssh_command(ip, command)

@mcp.tool()
def colcon_build(ip: str, workspace_path: str, packages: str = "") -> str:
    """Run colcon build on the robot."""
    pkg_arg = f"--packages-select {packages}" if packages else ""
    command = f"source /opt/ros/*/setup.bash && cd {workspace_path} && colcon build {pkg_arg}"
    return run_ssh_command(ip, command)

def main():
    mcp.run()

if __name__ == "__main__":
    main()
