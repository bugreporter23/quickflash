{ pkgs, ... }:
{
  imports = [ ./get-in.nix ];
  system.stateVersion = "26.05";
  nix.settings.experimental-features = [
    "nix-command"
    "flakes"
  ];
  programs.nix-ld.enable = true;
  hardware.enableRedistributableFirmware = true;
  hardware.cpu.intel.updateMicrocode = true;
  hardware.cpu.amd.updateMicrocode = true;
  environment.systemPackages = with pkgs; [
    python3
    nixos-facter
    pciutils
    usbutils
    util-linux
    iproute2
    ethtool
    curl
    git
    vim
  ];
}
