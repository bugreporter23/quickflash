{ identity }:
{ lib, pkgs, ... }:
{
  imports = [ ../modules/installed-base.nix ];
  networking.hostName = identity.name;
  networking.hostId = identity.host_id;
  time.timeZone = lib.mkIf (identity ? timezone) identity.timezone;
  environment.systemPackages = map (
    name: lib.getAttrFromPath (lib.splitString "." name) pkgs
  ) identity.packages;
  users.users = {
    root = {
      initialHashedPassword = identity.root_hashed_password or "!";
      openssh.authorizedKeys.keys = identity.ssh_keys;
    };
  }
  // lib.optionalAttrs (identity.user != null) {
    ${identity.user.name} = {
      isNormalUser = true;
      extraGroups = lib.optional identity.user.sudo "wheel";
      initialHashedPassword = identity.user.hashed_password or "!";
    };
  };
  security.sudo.wheelNeedsPassword = true;
  nix.nixPath = [ "nixpkgs=${builtins.storePath (toString pkgs.path)}" ];
}
