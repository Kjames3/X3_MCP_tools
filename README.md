# X3_MCP_tools

This repository provides an MCP (Model Context Protocol) server for interacting with the Jetson robot. It contains tools specifically designed to make it easy for an AI assistant to sync code, diagnose services, view ROS 2 topics, and compile code on the Jetson.

## Features

The server exposes the following tools.

### Services

- **`manage_service`**: Start, stop, restart, enable, disable, or query a systemd service. State-changing verbs run with root automatically (see *Privileged commands* below); `status` does not.
- **`restart_and_wait`**: Restart a service and block until it is genuinely ready, rather than until the process has merely been spawned. Give it a `ready_pattern` regex (for example `Server started on ws`) and it waits for that line to appear in the new journal output.
- **`check_service_status`**: Check the status of a systemd service.
- **`list_services`**: List services in `active`, `failed`, or `all` states.
- **`restart_service_privileged`**: Deprecated -- `manage_service(service, "restart")` now does the same thing. Kept for existing callers.

### Logs and diagnosis

- **`journal_grep`**: Search a service's journal with a time window (`since`), an include regex, and an exclude regex. The exclude is usually what makes a chatty log readable.
- **`diagnose_errors`**: Retrieve the last lines of a systemd service journal, or tail a log file.

### Code sync

- **`diff_code`**: Compare a laptop directory against its counterpart on the robot and report what genuinely differs, in which direction, without changing anything. Comparison is by checksum, so a file that was merely re-copied is not reported as drift; build artifacts and backup files are excluded by default. Pass `show_diff` a filename for a unified diff of that one file.
- **`sync_code`**: rsync between laptop and robot. `direction` is `push` or `pull`. **`dry_run` defaults to `True`** -- the first call only reports what would change. `--delete` is off unless you ask for it.
- **`colcon_build`**: Run `colcon build` on the robot's workspace. Defaults to a 15-minute timeout.

### ROS 2

- **`check_ros_status`**: List ROS 2 nodes, topics, and services.
- **`check_ros_node`**: Inspect a ROS 2 node and search logs for errors.
- **`view_ros_topic`**: Read one message from a ROS 2 topic.
- **`check_rosbag_info`** / **`check_rosbag_topics`**: Inspect a recorded bag file.

### Hardware and health

- **`check_robot_devices`**: Verify that the robot's serial device *symlinks* resolved (`/dev/openrb150`, `/dev/rosmaster`, `/dev/lx16a`), alongside the raw `ttyUSB`/`ttyACM` nodes and the installed udev rules. `lsusb` tells you a board enumerated; this tells you whether the stable name the code actually opens exists.
- **`check_gui_health`**: Diagnose why the GUI or its websocket is unreachable from a browser -- checks the unit is running, that it is listening on the expected ports, and that those ports accept a connection from this laptop.
- **`cleanup_strays`**: Find and optionally kill leftover processes matching a pattern, so orphaned test nodes do not poison the next measurement. `dry_run` defaults to `True`.
- **`check_connection`**: Verify network reachability and SSH access.
- **`check_system_resources`**, **`check_gpu_status`**, **`check_temperature`**, **`check_disk_usage`**, **`check_network`**, **`check_usb_devices`**, **`check_uptime_and_boot`**: General health checks.

## Configuration

Every tool takes an optional `ip`. When you omit it, the server uses `JETSON_HOST` and falls back through `JETSON_FALLBACK_HOSTS` -- so a robot that is sometimes on the LAN and sometimes only on Tailscale does not need the address restated on every call. The host that answered is remembered for the rest of the process.

| Variable | Default | Purpose |
| --- | --- | --- |
| `JETSON_HOST` | `x3` | Primary hostname or IP. |
| `JETSON_FALLBACK_HOSTS` | `100.64.52.55` | Comma-separated addresses tried when the primary does not answer. |
| `JETSON_SSH_USER` | `jetson` | SSH user. |
| `JETSON_ROS_DISTRO` | `humble` | ROS 2 distro to source. |
| `JETSON_ROS_DOMAIN_ID` | `42` | `ROS_DOMAIN_ID` exported before every ROS command. |
| `JETSON_ROS_WORKSPACE` | `~/x3_ws` | Workspace whose `install/setup.bash` overlay is sourced. |
| `JETSON_SUDO_PASSWORD` | *(unset)* | Sudo password for privileged tools. Leave unset if you have a NOPASSWD sudoers rule. |

### The ROS environment matters

ROS tools source the distro **and** the workspace overlay, export `ROS_DOMAIN_ID`, and clear `ROS_DISCOVERY_SERVER`. Sourcing only `/opt/ros/*/setup.bash` returns an *empty* node and topic list on this robot -- the tools appear to work while reporting nothing.

### Privileged commands

`sudo` over a non-interactive SSH session has no tty and fails with `a terminal is required to read the password`. There are two supported ways around it.

**Preferred -- a scoped sudoers rule on the robot (installed).** `/etc/sudoers.d/x3-mcp` grants the `jetson` user passwordless sudo for exactly `systemctl {start,stop,restart,enable,disable}` on the `x3_*` units, and nothing else. No shell, no wildcards, no `daemon-reload`. Everything outside that list still requires a password. `JETSON_SUDO_PASSWORD` can stay unset.

Because sudoers matches literal command lines, privileged service calls go through `systemctl_privileged()`, which runs exactly `sudo -n -- /usr/bin/systemctl <verb> <unit>`. It deliberately does not use the `bash -c` wrapper: authorizing `bash -c` in sudoers would be equivalent to granting full root. Anything that needs to happen around the state change -- `is-active`, reading the journal -- runs unprivileged as a separate command, which works because the `jetson` user is in the `adm` group.

Remove the rule with `sudo rm /etc/sudoers.d/x3-mcp`. To grant a newly added unit, add it to the rule and re-validate with `visudo -c`.

**Fallback -- a stored password.** Set `JETSON_SUDO_PASSWORD` and privileged commands feed it on stdin via `sudo -S -p ''`. This also enables `run_privileged_command()`, the general escape hatch for arbitrary root commands, which wraps in `bash -c` so pipes and `&&` run as root too. With neither configured, privileged calls fail immediately with a clear message rather than hanging.

### Timeouts

`run_ssh_command` defaults to 60 seconds and accepts a `timeout_s` per call; `colcon_build` defaults to 900. Builds, sweeps, and benchmarks on this robot routinely need minutes.

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
