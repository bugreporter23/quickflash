{ config, ... }:
let
  zfs = config.boot.kernelPackages.zfs_2_4;
in
{
  assertions = [
    {
      assertion = zfs.version == "2.4.4";
      message = "Review Quickflash's zvol completion backport when changing OpenZFS 2.4.4.";
    }
  ];
  boot.zfs.modulePackage = zfs.overrideAttrs (old: {
    patches = (old.patches or [ ]) ++ [ ../patches/openzfs/zvol-set-blocking.patch ];
  });
}
