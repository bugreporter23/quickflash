{ lib, ... }:
{
  networking.networkmanager.enable = true;
  networking.networkmanager.settings = {
    main.autoconnect-retries-default = 0;
    connection-wifi = {
      match-device = "type:wifi";
      "ipv4.route-metric" = 600;
      "ipv6.route-metric" = 600;
    };
    connection-ethernet = {
      match-device = "type:ethernet";
      "ipv4.route-metric" = 100;
      "ipv6.route-metric" = 100;
    };
    connectivity = {
      enabled = lib.mkDefault true;
      uri = lib.mkDefault "https://nmcheck.gnome.org/check_network_status.txt";
      response = "NetworkManager is online";
      interval = 60;
      timeout = 10;
    };
  };
  networking.firewall.checkReversePath = "loose";
  networking.useDHCP = false;
  hardware.facter.detected.dhcp.enable = false;
  services.openssh = {
    enable = true;
    hostKeys = lib.mkDefault [
      {
        type = "ed25519";
        path = "/etc/ssh/ssh_host_ed25519_key";
      }
    ];
    settings = {
      PermitRootLogin = "prohibit-password";
      PasswordAuthentication = false;
      KbdInteractiveAuthentication = false;
    };
  };
  services.avahi = {
    enable = true;
    nssmdns4 = true;
    publish = {
      enable = true;
      addresses = true;
    };
  };
}
