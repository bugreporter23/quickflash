"""USB preparation and local access for the fixed NixOS ZFS installer."""

import contextlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

from . import transport, trust
from .disk import mounted
from .sizing import cache_overlap
from .trust import validate_public_keys
from .wifi import keyfile

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parent
NIX = "nix"
PROVISION_LABEL = "quickflash-provision-v1"


def run(args, capture=False, **kwargs):
    return subprocess.run(
        [str(x) for x in args],
        check=True,
        stdout=subprocess.PIPE if capture else None,
        text=True,
        **kwargs,
    ).stdout


def nix_environment():
    env = dict(os.environ)
    env["NIX_CONFIG"] = (
        env.get("NIX_CONFIG", "") + "\nsandbox = true\nsandbox-fallback = false\n"
    )
    return env


def nix(*args, capture=False):
    return run(
        [
            NIX,
            "--extra-experimental-features",
            "nix-command flakes",
            "--option",
            "sandbox",
            "true",
            "--option",
            "sandbox-fallback",
            "false",
            *args,
        ],
        capture,
        env=nix_environment(),
    )


def target(value):
    if "@" not in value:
        name = hostname(value)
        if len(name) > 53:
            raise ValueError("Bootstrap name must be at most 53 characters")
        return f"root@{name}-bootstrap.local"
    if not re.fullmatch(r"root@[A-Za-z0-9][A-Za-z0-9.:-]*", value):
        raise ValueError(
            "Use a provisioned name, e.g. compute-a, or an explicit root@address"
        )
    return value


def hostname(value):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", value) or value.endswith("-"):
        raise ValueError("Host name must be a lowercase DNS label")
    if value in ("test", "bootstrap"):
        raise ValueError("Reserved host name")
    return value


def external_path(value):
    path = Path(value).expanduser().resolve()
    repository = next((p for p in ROOT.parents if (p / ".git").exists()), ROOT)
    if path == repository or repository in path.parents:
        raise ValueError(
            "Deployment inputs and outputs must be outside the code repository"
        )
    return path


def ssh(host, command, config, *, timeout=30, capture=True):
    result = transport.remote(target(host), command, config, timeout=timeout)
    if capture:
        return result
    print(result, end="")


def confirm(description):
    if not sys.stdin.isatty():
        raise ValueError("Destructive operations require an interactive terminal")
    token = secrets.token_urlsafe(6)
    print("\n" + "=" * 64)
    print("CONFIRM ACTION\n\n" + description)
    print("\nPress Ctrl-C to cancel.")
    print("=" * 64)
    if input(f'Type "{token}" (without quotes) to continue: ') != token:
        raise ValueError("Confirmation did not match; action cancelled")


@contextlib.contextmanager
def snapshot():
    with tempfile.TemporaryDirectory(prefix="quickflash-source-") as tmp:
        dest = Path(tmp)
        for name in ("flake.nix", "flake.lock"):
            shutil.copyfile(ROOT / name, dest / name)
        for name in (
            "modules",
            "patches",
            "disk",
            "install",
            "bootstrap",
            "bin",
            "quickflash",
        ):
            (dest / name).mkdir()
            for src in (ROOT / name).rglob("*"):
                if src.is_file() and (
                    src.suffix in (".nix", ".py", ".patch")
                    or src.name in ("quickflash", "quickadmin")
                ):
                    if src.is_symlink():
                        raise ValueError(f"Source symlinks are unsupported: {src}")
                    out = dest / src.relative_to(ROOT)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, out)
        yield f"path:{dest}"


def probe(host, config):
    script = (PACKAGE / "probe.py").read_text()
    return json.loads(ssh(host, "python3 -c " + shlex.quote(script), config))


def size(value):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024


def disk_description(disk):
    return (
        f"{disk.get('name', 'Disk')}  {size(disk['size'])}\n"
        f"  Model: {str(disk.get('model') or 'unknown').strip()}\n"
        f"  Serial: {disk.get('serial') or 'unknown'}"
    )


def show_probe(info):
    environment = "quickflash bootstrap" if info["bootstrap"] else "installed system"
    print(f"{info['architecture']} · {info['boot_mode'].upper()} · {environment}")
    cpu = dict(line.split(":", 1) for line in info["cpu"].splitlines() if ":" in line)
    if "Model name" in cpu:
        print("CPU: " + cpu["Model name"].strip())
    memory = next(
        (
            line.split()
            for line in info["memory"].splitlines()
            if line.startswith("Mem:")
        ),
        None,
    )
    if memory:
        print("Memory: " + memory[1])
    for interface in info["interfaces"]:
        if interface["ifname"] == "lo":
            continue
        addresses = [
            v["local"]
            for v in interface.get("addr_info", [])
            if v.get("scope") == "global"
        ]
        if addresses:
            print(f"Network: {interface['ifname']}  {', '.join(addresses)}")
    print("\nWhole disks:")
    for disk in info["disks"]:
        if disk["type"] != "disk":
            continue
        print("\n" + disk_description(disk))
        reason = (
            "read-only"
            if disk.get("ro")
            else "in use / mounted"
            if mounted(disk)
            else None
        )
        if reason:
            print("  Cannot flash: " + reason)
        elif disk.get("ids"):
            print("  Select: " + disk["ids"][0])
        else:
            print("  Cannot select: no stable /dev/disk/by-id identifier")


def health(args):
    script = (PACKAGE / "health.py").read_text()
    report = json.loads(
        ssh(
            args.target,
            "timeout 90s python3 -c " + shlex.quote(script),
            args.connection,
            timeout=105,
        )
    )
    if not report["checks"]:
        raise ValueError("Target returned no functional health checks")
    report["checks"].insert(
        0,
        {
            "name": "ssh-session",
            "status": "pass",
            "detail": "Authenticated SSH executed the target checks and returned results",
        },
    )
    passed = all(check["status"] == "pass" for check in report["checks"])
    report["functional_checks_passed"] = passed
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"Health · {args.target} · {'PASS' if passed else 'INCOMPLETE / FAILED'}\n"
        )
        for check in report["checks"]:
            print(f"{check['status'].upper():7} {check['name']}: {check['detail']}")
        print("\nKernel observations (separate from functional results):")
        kernel = report["kernel"]
        if "error" in kernel:
            print(f"  Unavailable: {kernel['error']}")
        else:
            print(
                f"  {kernel['sampled_entries']} warning/error entries sampled (latest {kernel['sample_limit']} this boot)"
            )
            print(
                f"  PCIe reports: {kernel['correctable_pcie']} correctable, {kernel['uncorrectable_pcie']} uncorrectable"
            )
            print(
                "  Old-microcode warning in sample: "
                + ("yes" if kernel["old_microcode"] else "not seen")
            )
        print(
            "\nThese are current runtime checks, not image boot or hardware acceptance."
        )
        print("Use health --json for the full report and acceptance limits.")
    if not passed:
        raise SystemExit(1)


def select_disk(info, disk_id):
    if not disk_id.startswith("/dev/disk/by-id/"):
        raise ValueError("Select a whole disk by /dev/disk/by-id/...")
    matches = [d for d in info["disks"] if disk_id in d.get("ids", [])]
    if len(matches) != 1 or matches[0]["type"] != "disk":
        raise ValueError("Disk ID must resolve to exactly one whole disk")
    d = matches[0]
    if d.get("ro") or mounted(d):
        raise ValueError(
            "Refusing a read-only or mounted disk (including bootstrap media)"
        )
    return d


def write_secret(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value)


def local_disk(device):
    if not str(device).startswith("/dev/") or not stat.S_ISBLK(os.stat(device).st_mode):
        raise ValueError("Expected a block device under /dev/")
    data = json.loads(
        run(
            [
                "lsblk",
                *(["--properties-by", "blkid"] if os.geteuid() == 0 else []),
                "--json",
                "--bytes",
                "--paths",
                "-o",
                "NAME,TYPE,SIZE,MODEL,SERIAL,RO,MOUNTPOINTS,FSTYPE,LOG-SEC,PARTLABEL",
                device,
            ],
            True,
        )
    )
    if len(data["blockdevices"]) != 1:
        raise ValueError("Expected one whole disk")
    d = data["blockdevices"][0]
    if d["type"] != "disk" or d.get("ro") or mounted(d):
        raise ValueError("Expected an unmounted, writable whole disk")
    return d


def sudo_self(arguments, **kwargs):
    return run(
        [
            "sudo",
            "--",
            shutil.which("env") or "env",
            "PATH=" + os.environ["PATH"],
            sys.executable,
            str(ROOT / "bin/quickflash"),
            *arguments,
        ],
        **kwargs,
    )


@contextlib.contextmanager
def provision_mount(device):
    disk = local_disk(device)
    partitions = [
        p for p in disk.get("children", []) if p.get("partlabel") == PROVISION_LABEL
    ]
    if len(partitions) != 1 or partitions[0].get("fstype") != "ext4":
        raise ValueError(
            "Expected a v1 provisioning partition; legacy USBs need a full rewrite for this operation"
        )
    with tempfile.TemporaryDirectory(prefix="quickflash-usb-") as tmp:
        run(["mount", "-o", "nosuid,nodev,noexec", partitions[0]["name"], tmp])
        try:
            root = Path(tmp).resolve()
            marker = root / "layout-version"
            if (
                marker.is_symlink()
                or not marker.is_file()
                or marker.read_text() != "1\n"
            ):
                raise ValueError("Unrecognized USB provisioning layout")
            yield root
        finally:
            run(["umount", tmp])


def replace_provision_file(root, relative, value):
    if (
        root.resolve() != root
        or Path(relative).is_absolute()
        or ".." in Path(relative).parts
    ):
        raise ValueError("Refusing a provisioning path outside the mounted partition")
    path = root / relative
    for directory in [root, *path.relative_to(root).parents]:
        directory = directory if directory.is_absolute() else root / directory
        if directory.is_symlink():
            raise ValueError("Refusing symlinked provisioning directories")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("Refusing nonregular provisioning file")
    temporary = path.with_name(f".quickflash-{uuid.uuid4()}")
    try:
        write_secret(temporary, value)
        with temporary.open("rb") as file:
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def ensure_bootstrap_host_key(root):
    path = root / "ssh/ssh_host_ed25519_key"
    trust.regular(path, missing=True)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    if not path.exists():
        run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", path])
    if path.stat().st_mode & 0o777 != 0o600 or path.stat().st_uid != os.getuid():
        raise ValueError(
            "USB host private key must be owned by the provisioning user with mode 0600"
        )
    public = trust.public_key(
        run(["ssh-keygen", "-y", "-P", "", "-f", path], capture=True)
    )
    replace_provision_file(root, "ssh/ssh_host_ed25519_key.pub", public + "\n")
    return public


def partition_table(path, privileged=False):
    executable = shutil.which("sfdisk")
    if executable is None:
        raise ValueError("Missing sfdisk; run inside nix develop")
    command = [executable, "--json", str(path)]
    if privileged and os.geteuid() != 0:
        command = ["sudo", "--", *command]
    return json.loads(run(command, capture=True))["partitiontable"]


def installer_output(source, target_module, usb_image_size, attribute, *, build=True):
    env = nix_environment()
    env["QUICKFLASH_SOURCE"] = source
    env["QUICKFLASH_TARGET_MODULE"] = str(target_module) if target_module else ""
    env["QUICKFLASH_USB_IMAGE_SIZE"] = usb_image_size
    output = run(
        [
            NIX,
            "--extra-experimental-features",
            "nix-command flakes",
            *(
                ["build", "--no-link", "--print-out-paths"]
                if build
                else ["eval", "--json"]
            ),
            "--impure",
            "--no-update-lock-file",
            "--expr",
            """
              let
                source = builtins.getFlake (builtins.getEnv "QUICKFLASH_SOURCE");
                module = builtins.getEnv "QUICKFLASH_TARGET_MODULE";
              in (source.lib.mkInstaller {
                targetModule = if module == "" then null else builtins.storePath module;
                usbImageSize = builtins.getEnv "QUICKFLASH_USB_IMAGE_SIZE";
              })
            """
            + "."
            + attribute,
        ],
        capture=True,
        env=env,
    )
    return output.strip()


def build_bootstrap_image(source, target_module=None, usb_image_size="6G"):
    output = installer_output(source, target_module, usb_image_size, "image")
    image = Path(output) / "main.raw"
    if not image.is_file() or image.stat().st_size == 0:
        raise ValueError("Bootstrap build did not produce a nonempty main.raw image")
    return image


def image_size_bytes(value):
    match = re.fullmatch(r"([0-9]+)([KMGT]?)", value, re.IGNORECASE)
    if not match or int(match[1]) == 0:
        raise ValueError(
            "Use a positive USB image size in bytes or K/M/G/T units, e.g. 6G"
        )
    return int(match[1]) * 1024 ** (
        "KMGT".index(match[2].upper()) + 1 if match[2] else 0
    )


def check_bytes_fit(size, disk):
    if disk.get("log-sec") != 512:
        raise ValueError("Raw image requires a disk with 512-byte logical sectors")
    if not size or size % 512 or size > disk["size"]:
        raise ValueError(
            f"Image ({size:,} bytes) must be sector-aligned and fit the disk "
            f"({disk['size']:,} bytes)"
        )


def check_image_fits(image, disk):
    check_bytes_fit(image.stat().st_size, disk)


def estimate_bootstrap_image(source, target_module, usb_image_size):
    metadata = json.loads(
        installer_output(source, target_module, usb_image_size, "sizing", build=False)
    )
    system = installer_output(source, target_module, usb_image_size, "usbSystem")
    paths = json.loads(
        nix(
            "path-info",
            "--json",
            "--json-format",
            "1",
            "--recursive",
            system,
            capture=True,
        )
    )
    content = sum(info["narSize"] for info in paths.values())
    fixed = (
        sum(image_size_bytes(value) for value in metadata["partitions"].values())
        + 2 * 1024**2
    )
    margin = max(512 * 1024**2, (content * 15 + 99) // 100)
    minimum = content + fixed + margin
    gib = 1024**3
    return {
        "content": content,
        "cache": paths[metadata["cache"]]["narSize"],
        "shared_paths": len(
            (Path(metadata["cache"]) / "shared-paths").read_text().splitlines()
        ),
        **cache_overlap(metadata["cache"], paths),
        "fixed": fixed,
        "margin": margin,
        "minimum": minimum,
        "recommended": (minimum + gib - 1) // gib,
    }


def image_write_commands(image, device):
    commands = []
    for tool, arguments in (
        (
            "dd",
            [
                f"if={image}",
                f"of={device}",
                "bs=4M",
                "iflag=fullblock",
                "oflag=direct",
                "conv=fsync,notrunc",
                "status=progress",
            ],
        ),
        ("sfdisk", ["--lock=yes", "--relocate", "gpt-bak-std", device]),
        ("blockdev", ["--flushbufs", device]),
    ):
        executable = shutil.which(tool)
        if executable is None:
            raise ValueError(f"Missing {tool}; run inside nix develop")
        commands.append([executable, *arguments])
    return commands


def write_usb_image(image, device, selected):
    """Approve and write a prepared image to the previously inspected USB."""
    check_image_fits(image, selected)
    commands = image_write_commands(image, device)
    confirm(
        f"Erase the entire USB {device} and write {image}.\n"
        + disk_description(selected)
        + "\nAll existing USB contents will be destroyed."
    )
    run(["sudo", "-v"])
    if local_disk(device) != selected:
        raise ValueError("USB changed since approval; nothing written")
    print("Writing USB image...", flush=True)
    for command in commands:
        run(["sudo", "--", *command])


def provisioning(value):
    """Validate the whole optional JSON payload before touching a USB."""
    if (
        not isinstance(value, dict)
        or not value
        or set(value) - {"name", "ssh_keys", "wifi"}
    ):
        raise ValueError(
            "Provisioning JSON must contain only name, ssh_keys, and/or wifi"
        )
    files = {}
    if "name" in value:
        if not isinstance(value["name"], str) or len(hostname(value["name"])) > 53:
            raise ValueError(
                "Provisioning name must be a DNS label of at most 53 characters"
            )
        files["hostname"] = value["name"] + "\n"
    if "ssh_keys" in value:
        keys = value["ssh_keys"]
        if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
            raise ValueError("ssh_keys must be an array of public key strings")
        files["authorized_keys"] = "\n".join(validate_public_keys(keys)) + "\n"
    if "wifi" in value:
        wifi = value["wifi"]
        if (
            not isinstance(wifi, dict)
            or set(wifi) != {"ssid", "password"}
            or not all(isinstance(v, str) for v in wifi.values())
        ):
            raise ValueError("wifi must contain ssid and password strings")
        files["system-connections/fabric-wifi.nmconnection"] = keyfile(
            wifi["ssid"], wifi["password"]
        )
    return files


def provision_file(path):
    path = external_path(path)
    if path.stat().st_mode & 0o077:
        raise ValueError("Provisioning file must be private: chmod 600 FILE")
    value = json.loads(path.read_text())
    provisioning(value)
    return value


def put_provision(device, value):
    files = provisioning(value)
    if os.geteuid() != 0:
        sudo_self(
            ["bootstrap", "provision", device, "--config-stdin"],
            input=json.dumps(value),
        )
        return
    with provision_mount(device) as root:
        for name, contents in files.items():
            replace_provision_file(root, name, contents)
        ensure_bootstrap_host_key(root)
    print("USB settings provisioned; omitted fields preserved. USB is unmounted.")


def bootstrap(args):
    if args.action == "provision":
        value = json.load(sys.stdin) if args.config_stdin else provision_file(args.file)
        put_provision(args.device, value)
        return
    if args.action == "reset":
        local_disk(args.device)
        if os.geteuid() != 0:
            sudo_self(["bootstrap", "reset", args.device])
            return
        confirm(
            f"Reset provisioning on USB {args.device}.\n"
            "Remove its name, Wi-Fi settings, authorized keys, and bootstrap SSH host keys.\n"
            "The USB OS is preserved. You must provision access again before remote use."
        )
        with provision_mount(args.device) as root:
            for path in root.iterdir():
                if path.name == "layout-version":
                    continue
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            for name in ("system-connections", "ssh"):
                (root / name).mkdir(mode=0o700)
        print("USB provisioning reset. Apply your USB JSON file before remote use.")
        return
    config = provision_file(args.provision_file)
    if not config.get("name") or not config.get("ssh_keys"):
        raise ValueError("USB preparation requires name and administrator ssh_keys")
    if getattr(args, "target_module", None) is not None:
        args.target_module = args.target_module.resolve(strict=True)
        if not args.target_module.is_file():
            raise ValueError("Target module must be a built NixOS module file")
    with snapshot() as source:
        selected = local_disk(args.device) if args.action == "write" else None
        target_module = getattr(args, "target_module", None)
        image_size = getattr(args, "usb_image_size", "6G")
        if selected is not None:
            planned = image_size_bytes(image_size)
            print(f"Selected target: {target_module or 'Quickflash base'}", flush=True)
            print(f"Configured raw image: {size(planned)} ({planned:,} bytes)")
            print(f"Selected USB: {args.device} ({size(selected['size'])})")
            check_bytes_fit(planned, selected)
            print(f"Expected bytes written: {planned:,}")
            print(f"Device headroom: {size(selected['size'] - planned)}", flush=True)
            print(
                "Measuring USB requirements; realizing system/cache, not the raw image...",
                flush=True,
            )
            estimate = estimate_bootstrap_image(source, target_module, image_size)
            print(f"USB closure content: {size(estimate['content'])}")
            print(f"  Embedded installation cache: {size(estimate['cache'])}")
            print(f"  Target paths supplied by live USB: {estimate['shared_paths']}")
            print(
                f"  Cache copies of {estimate['shared_cache_paths']} live store paths: "
                f"{size(estimate['shared_cache_bytes'])} compressed"
            )
            print(f"Fixed partitions/GPT: {size(estimate['fixed'])}")
            print(f"Filesystem safety margin: {size(estimate['margin'])}")
            print(f"Estimated required image: {size(estimate['minimum'])}")
            print(f"Recommended image size: {estimate['recommended']} GiB", flush=True)
            if planned < estimate["minimum"]:
                raise ValueError(
                    f"Configured image {image_size} is too small for the conservative estimate. "
                    f"Retry with --usb-image-size {estimate['recommended']}G "
                    "and a USB large enough for that image"
                )
            print(
                f"Estimated image headroom: {size(planned - estimate['minimum'])}",
                flush=True,
            )
        print(f"Building the {image_size} bootable USB image locally...", flush=True)
        image = build_bootstrap_image(source, target_module, image_size)
        print(f"Bootstrap image: {image} ({image.stat().st_size:,} bytes)", flush=True)
        if args.action == "write":
            write_usb_image(image, args.device, selected)
            put_provision(args.device, config)
            print("USB prepared with the ZFS installer and access settings.")
