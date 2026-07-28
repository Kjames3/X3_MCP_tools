# X3_MCP_tools

This repository provides an MCP (Model Context Protocol) server for interacting with the Jetson robot. It contains tools specifically designed to make it easy for an AI assistant to sync code, diagnose services, view ROS 2 topics, and compile code on the Jetson.

## Features

The server exposes the following tools:
- **`sync_code`**: Syncs code from a local directory on your laptop to a remote directory on the Jetson using `rsync` over SSH.
- **`view_ros_topic`**: Reads data directly from a ROS 2 topic on the Jetson.
- **`diagnose_errors`**: Retrieves logs for a systemd service or tail a log file on the Jetson to help diagnose issues.
- **`colcon_build`**: Triggers a `colcon build` on the Jetson robot's workspace.
- **`check_connection`**: Verifies network reachability and SSH access to the Jetson.
- **`check_disk_usage`**: Reports remote disk usage for a specific path.
- **`check_system_resources`**: Returns CPU, memory, and process usage on the Jetson.
- **`check_gpu_status`**: Shows GPU/accelerator status via `nvidia-smi` and `tegrastats`.
- **`check_temperature`**: Reads remote thermal sensor values.
- **`check_ros_status`**: Lists ROS 2 nodes, topics, and services.
- **`check_ros_logs`**: Finds ROS-related journal or package logs.
- **`check_service_status`**: Checks the status of a systemd service.
- **`list_services`**: Lists services in `active`, `failed`, or `all` states.
- **`manage_service`**: Starts, stops, restarts, enables, disables, or queries service status.
- **`check_ros_node`**: Inspects a ROS 2 node and searches logs for errors.
- **`check_rosbag_info`**: Shows `ros2 bag info` for a recorded bag file.
- **`check_rosbag_topics`**: Extracts the topic section from `ros2 bag info` output.
- **`check_network`**: Inspects network interfaces, routes, and DNS configuration.
- **`check_usb_devices`**: Lists attached USB devices and camera hardware.
- **`check_uptime_and_boot`**: Shows uptime, reboots, and recent kernel logs.

*Note: For each of these tools, you will need to provide the IP address of the Jetson robot, as it may change between sessions.*

## Setup

First, make sure you have `mcp` installed. If you use `uv`, you can run the server directly using `uvx`, or you can install the dependencies into a virtual environment.

```bash
# In the repository root
pip install -e .
```

## IDE Configuration

### Antigravity IDE

You can add this MCP server to Antigravity IDE by configuring it in your `mcp.json` or through the UI as a standard stdio server:

**Command**: `uvx`
**Args**: ["--from", ".", "jetson-mcp"]

Alternatively, using the python environment where it is installed:
**Command**: `python`
**Args**: `["-m", "jetson_mcp.server"]`

### Claude Code

To add this server to Claude Code, you can use the `mcp add` command:

```bash
claude mcp add jetson-mcp uvx --from . jetson-mcp
```