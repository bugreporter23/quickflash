{
  config,
  lib,
  modulesPath,
  pkgs,
  ...
}:
{
  imports = [
    (modulesPath + "/profiles/all-hardware.nix")
    (modulesPath + "/profiles/minimal.nix")
  ];
  nixpkgs.flake.setNixPath = false;
  nixpkgs.flake.setFlakeRegistry = false;
  programs.nix-ld.enable = lib.mkForce false;
  system.tools = {
    nixos-rebuild.enable = false;
    nixos-option.enable = false;
    nixos-build-vms.enable = false;
  };
  boot.initrd.systemd.enable = true;
  boot.initrd.systemd.additionalUpstreamUnits = [ "systemd-volatile-root.service" ];
  boot.initrd.systemd.services.systemd-volatile-root.before = [ "sysroot-run.mount" ];
  boot.initrd.systemd.storePaths = [
    "${config.boot.initrd.systemd.package}/lib/systemd/systemd-volatile-root"
  ];
  fileSystems."/" = {
    noCheck = true;
  };
  fileSystems."/boot" = {
    noCheck = true;
    options = [ "ro" ];
  };
  fileSystems."/var/lib/fabric-provision" = {
    noCheck = true;
    options = [
      "ro"
      "noload"
    ];
  };
  networking.hostName = "fabric-bootstrap";
  environment.etc.hostname.enable = false;
  services.avahi.hostName = "";
  networking.networkmanager.settings.main.hostname-mode = "none";
  networking.networkmanager.settings.keyfile.path = "/run/NetworkManager/system-connections";
  users.users.root.openssh.authorizedKeys.keys = lib.mkForce [ ];
  services.openssh.authorizedKeysFiles = lib.mkForce [ "/run/quickflash/authorized_keys" ];
  services.openssh.hostKeys = [
    {
      type = "ed25519";
      path = "/run/quickflash/ssh/ssh_host_ed25519_key";
    }
  ];
  disko.devices.disk.main = {
    imageSize = "3G";
    content.partitions.ESP.size = lib.mkForce "128M";
    content.partitions.ESP.priority = 2;
    content.partitions.provision = {
      label = "quickflash-provision-v1";
      priority = 3;
      size = "16M";
      content = {
        type = "filesystem";
        format = "ext4";
        mountpoint = "/var/lib/fabric-provision";
        mountOptions = [
          "nosuid"
          "nodev"
          "noexec"
        ];
        postMountHook = ''
          provision=${lib.escapeShellArg "${config.disko.rootMountPoint}/var/lib/fabric-provision"}
          chmod 0700 "$provision"
          install -d -m 0700 "$provision/system-connections" "$provision/ssh"
          printf '1\n' > "$provision/layout-version"
          chmod 0600 "$provision/layout-version"
        '';
      };
    };
  };
  systemd.services.fabric-bootstrap-provision = {
    description = "Apply disposable bootstrap provisioning";
    wantedBy = [ "multi-user.target" ];
    requiredBy = [
      "NetworkManager.service"
      "avahi-daemon.service"
      "sshd.service"
      "sshd-keygen.service"
    ];
    before = [
      "NetworkManager.service"
      "avahi-daemon.service"
      "sshd.service"
      "sshd-keygen.service"
    ];
    after = [
      "local-fs.target"
      "dbus.service"
    ];
    unitConfig.RequiresMountsFor = [ "/var/lib/fabric-provision" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${pkgs.python3}/bin/python3 ${./provision.py} ${pkgs.systemd}/bin/hostnamectl";
    };
  };
  boot.kernelParams = [
    "systemd.volatile=overlay"
    "ro"
    "console=tty0"
    "console=ttyS0,115200"
  ];
  boot.initrd.availableKernelModules = [
    "overlay"
    "xhci_pci"
    "ehci_pci"
    "uhci_hcd"
    "usb_storage"
    "uas"
    "sd_mod"
    "ahci"
    "nvme"
    "ata_piix"
    "virtio_pci"
    "virtio_blk"
    "virtio_scsi"
  ];
  services.getty.autologinUser = "root";
  environment.etc."fabric-bootstrap".text = "POC1\n";
  system.nixos.tags = [ "fabric-bootstrap" ];
}
