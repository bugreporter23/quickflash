{ pkgs }:
let
  openssh = pkgs.openssh.override { withKerberos = true; };
  python = pkgs.python3.withPackages (p: [ p.zeroconf ]);
  helper = pkgs.runCommand "quickflash-mdns-connect" { } ''
    mkdir -p $out/bin $out/share/quickflash
    cp ${./mdns_connect.py} $out/bin/quickflash-mdns-connect
    substituteInPlace $out/bin/quickflash-mdns-connect \
      --replace-fail '#!/usr/bin/env python3' '#!${python}/bin/python3' \
      --replace-fail 'SOCAT = "socat"' 'SOCAT = "${pkgs.socat}/bin/socat"'
    chmod +x $out/bin/quickflash-mdns-connect
    cat > $out/share/quickflash/ssh_config <<EOF
    Match host *.local,*.local.
      ProxyCommand $out/bin/quickflash-mdns-connect %h %p
    Host *
    EOF
  '';
  config = pkgs.writeText "quickflash-ssh-config" ''
    Include ~/.ssh/config
    Host *
      Include /etc/ssh/ssh_config
    Host *
      Include ${helper}/share/quickflash/ssh_config
  '';
  clients = pkgs.runCommand "quickflash-openssh" { } ''
    mkdir -p $out/bin
    cat > $out/bin/quickflash-ssh-config <<EOF
    #!${pkgs.runtimeShell}
    printf '%s\n' '${config}'
    EOF
    chmod +x $out/bin/quickflash-ssh-config
    ln -s ${openssh}/bin/* $out/bin/
    rm $out/bin/ssh $out/bin/scp $out/bin/sftp
    for command in ssh scp sftp; do
      cat > $out/bin/$command <<EOF
    #!${pkgs.runtimeShell}
    exec ${openssh}/bin/$command -F ${config} "\$@"
    EOF
      chmod +x $out/bin/$command
    done
  '';
in
{
  inherit
    helper
    clients
    config
    openssh
    ;
}
