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
