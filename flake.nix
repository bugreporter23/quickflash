{
  description = "Fixed NixOS ZFS installation and scoped mDNS/SSH administration";
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    disko.url = "github:nix-community/disko";
    disko.inputs.nixpkgs.follows = "nixpkgs";
  };
  outputs =
    {
      self,
      nixpkgs,
      disko,
      ...
    }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      sshTools = import ./bin/ssh.nix { inherit pkgs; };
      python = pkgs.python3.withPackages (p: [
        p.zeroconf
        p.argcomplete
      ]);
      source = pkgs.lib.fileset.toSource {
        root = ./.;
        fileset = pkgs.lib.fileset.unions [
          ./flake.nix
          ./flake.lock
          ./bin/quickflash
          ./bin/quickadmin
          ./bin/mdns_connect.py
          ./bin/ssh.nix
          ./bootstrap/default.nix
          ./bootstrap/disk.nix
          ./bootstrap/provision.py
          ./disk
          ./install
          ./modules
          ./patches/openzfs/zvol-set-blocking.patch
          (pkgs.lib.fileset.fileFilter (file: file.hasExt "py") ./quickflash)
        ];
      };
      flashCli = pkgs.writeShellApplication {
        name = "quickflash";
        runtimeInputs = with pkgs; [
          nix
          python
          sshTools.clients
          coreutils
          util-linux
        ];
        text = ''
          exec python3 ${source}/bin/quickflash "$@"
        '';
      };
      adminCli = pkgs.writeShellApplication {
        name = "quickadmin";
        runtimeInputs = [
          python
          sshTools.clients
          pkgs.nix
          pkgs.bashInteractive
          pkgs.coreutils
        ];
        text = ''
          quickadmin_bin=$(dirname -- "$(readlink -f -- "$0")")
          export PATH="$quickadmin_bin:$PATH"
          exec python3 ${source}/bin/quickadmin "$@"
        '';
      };
      toolkit = pkgs.symlinkJoin {
        name = "quickflash-tools";
        paths = [
          flashCli
          adminCli
        ];
        meta.mainProgram = "quickflash";
      };
      installHost = pkgs.writeShellApplication {
        name = "quickflash-install";
        runtimeInputs = with pkgs; [
          python3
          openssh
          nix
          util-linux
          coreutils
          dosfstools
          systemd
        ];
        text = ''
          export PYTHONPATH=${source}
          exec python3 ${source}/quickflash/install.py "$@"
        '';
      };
      storage = import ./disk/install-zfs.nix;
    in
    {
      nixosConfigurations = {
        bootstrap = nixpkgs.lib.nixosSystem {
          inherit system;
          modules = [
            disko.nixosModules.disko
            ./modules/base.nix
            ./modules/openzfs-zvol-sync.nix
            ./bootstrap/disk.nix
            ./bootstrap/default.nix
            ({ config, ... }: {
              boot.supportedFilesystems = [ "zfs" ];
              networking.hostId = "51554943";
              boot.zfs.forceImportRoot = false;
              environment.systemPackages = [
                installHost
                config.boot.zfs.package
                config.system.build.nixos-install
                config.system.build.nixos-generate-config
              ];
            })
          ];
        };
      };
      nixosModules.get-in = import ./modules/get-in.nix;
      nixosModules.installed-base = import ./modules/installed-base.nix;
      nixosModules.openzfs-zvol-sync = import ./modules/openzfs-zvol-sync.nix;
      lib = {
        mkInstaller =
          {
            targetModule ? null,
            usbImageSize ? "6G",
            usbRootFilesystem ? "btrfs",
            cacheCompression ? "zstd",
          }:
          let
            targetModulePath = if targetModule == null then null else "${targetModule}";
            bootstrap = self.nixosConfigurations.bootstrap.config;
            liveRoots = [
              bootstrap.system.path
              bootstrap.system.modulesTree
              bootstrap.boot.kernelPackages.kernel
              bootstrap.hardware.firmware
            ];
            seed = nixpkgs.lib.nixosSystem {
              inherit system;
              modules = [
                ./modules/installed-base.nix
                {
                  networking.hostName = "quickflash-seed";
                  users.allowNoPasswordLogin = true;
                }
                ./install/cache-hardware.nix
              ]
              ++ nixpkgs.lib.optional (targetModule != null) targetModulePath;
            };
            entry = pkgs.writeText "configuration.nix" ''
              {
                imports = [
                  (import ${source}/install/configuration.nix {
                    identity = builtins.fromJSON (builtins.readFile ./identity.json);
                  })
                  ./hardware-configuration.nix
                  ${nixpkgs.lib.optionalString (targetModule != null) targetModulePath}
                ];
              }
            '';
            configuration = pkgs.runCommand "quickflash-configuration" { } ''
              mkdir -p "$out"
              cp ${entry} "$out/configuration.nix"
              cat > "$out/system.nix" <<'NIX'
              import <nixpkgs/nixos> { configuration = ./configuration.nix; }
              NIX
            '';
            cache =
              (pkgs.mkBinaryCache {
                name = "quickflash-install-cache";
                compression = cacheCompression;
                rootPaths = [
                  seed.config.system.build.toplevel
                  configuration
                  nixpkgs.outPath
                ];
              }).overrideAttrs
                (old: {
                  exportReferencesGraph = old.exportReferencesGraph // {
                    live = liveRoots;
                  };
                  buildCommand = ''
                    mkdir -p "$out"
                    jq -r '
                      (.live | INDEX(.path)) as $live |
                      .closure[] | select($live[.path] != null) | .path
                    ' "$NIX_ATTRS_JSON_FILE" > "$out/shared-paths"
                    jq '
                      (.live | INDEX(.path)) as $live |
                      .closure |= map(select($live[.path] == null))
                    ' "$NIX_ATTRS_JSON_FILE" > cache-inputs.json
                    jq -r '.closure[].path' cache-inputs.json > "$out/cached-paths"
                    export NIX_ATTRS_JSON_FILE="$PWD/cache-inputs.json"
                    ${old.buildCommand}
                  '';
                  unsafeDiscardReferences.out = true;
                });
            manifest = pkgs.writeText "quickflash-payload.json" (
              builtins.toJSON {
                inherit storage cache;
                seedSystem = builtins.unsafeDiscardStringContext "${seed.config.system.build.toplevel}";
                configuration = builtins.unsafeDiscardStringContext "${configuration}";
                nixpkgs = builtins.unsafeDiscardStringContext nixpkgs.outPath;
              }
            );
            usb = self.nixosConfigurations.bootstrap.extendModules {
              modules = [
                ({ lib, ... }: {
                  assertions = [
                    {
                      assertion = builtins.elem usbRootFilesystem [
                        "ext4"
                        "btrfs"
                      ];
                      message = "USB root filesystem must be ext4 or btrfs.";
                    }
                  ];
                  disko.devices.disk.main.imageSize = lib.mkForce usbImageSize;
                  disko.imageBuilder.extraRootModules = lib.optional (usbRootFilesystem == "btrfs") "btrfs";
                  system.extraDependencies = liveRoots;
                  disko.devices.disk.main.content.partitions.root.content = lib.mkIf (usbRootFilesystem == "btrfs") (
                    lib.mkForce {
                      type = "btrfs";
                      extraArgs = [ "-f" ];
                      mountpoint = "/";
                      mountOptions = [
                        "compress=zstd"
                        "noatime"
                      ];
                    }
                  );
                  systemd.services.systemd-remount-fs.unitConfig.ConditionKernelCommandLine = lib.mkIf (
                    usbRootFilesystem == "btrfs"
                  ) "!systemd.volatile";
                  environment.etc."quickflash-install/payload.json".source = manifest;
                })
              ];
            };
          in
          {
            image = usb.config.system.build.diskoImages;
            usbSystem = usb.config.system.build.toplevel;
            sizing = {
              imageSize = usb.config.disko.devices.disk.main.imageSize;
              inherit usbRootFilesystem cacheCompression;
              partitions = nixpkgs.lib.mapAttrs (_: partition: partition.size) (
                nixpkgs.lib.filterAttrs (
                  _: partition: partition.size != "100%"
                ) usb.config.disko.devices.disk.main.content.partitions
              );
              cache = builtins.unsafeDiscardStringContext "${cache}";
            };
            inherit
              seed
              usb
              configuration
              cache
              manifest
              ;
          };
      };
      packages.${system} = {
        default = toolkit;
        quickflash = flashCli;
        quickadmin = adminCli;
        quickflash-openssh = sshTools.clients;
        quickflash-mdns-connect = sshTools.helper;
        quickflash-install = installHost;
      };
      formatter.${system} = pkgs.nixfmt;
      devShells.${system}.default = pkgs.mkShell {
        shellHook = ''
          eval "$(register-python-argcomplete quickflash quickadmin bin/quickflash bin/quickadmin ./bin/quickflash ./bin/quickadmin)"
        '';
        packages = with pkgs; [
          flashCli
          adminCli
          bashInteractive
          nix
          python
          sshTools.clients
          sshTools.helper
          git
          util-linux
          coreutils
          nixfmt
        ];
      };
    };
}
