{
  pool = "bowl";
  espLabel = "quickflash-esp";
  zfsLabel = "quickflash-zfs";
  espSize = "1GiB";
  poolOptions.ashift = "12";
  rootOptions = {
    mountpoint = "none";
    compression = "zstd";
    atime = "off";
  };
  datasets = {
    "ROOT/nixos" = {
      mountpoint = "/";
      options.mountpoint = "legacy";
    };
    home = {
      mountpoint = "/home";
      options.mountpoint = "legacy";
    };
    nix = {
      mountpoint = "/nix";
      options.mountpoint = "legacy";
    };
  };
}
