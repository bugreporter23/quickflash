{ identity }:
{
  imports = [ ../modules/installed-base.nix ];
  networking.hostName = identity.name;
  users.users.root.openssh.authorizedKeys.keys = identity.ssh_keys;
}
