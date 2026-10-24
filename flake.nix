{
  description = "ios-safari-mcp: an MCP server that drives iOS 16.5 Safari in QEMU on the HackiOS kernel";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];   # the FHS build shell and QEMU's setup are Linux only
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAll (pkgs: rec {
        # The relay that runs inside the guest (guest/wi_relay.c), built for iOS arm64 without the iOS SDK:
        # clang compiles it against the prototypes in the source, ld64.lld links it against the stub libraries in
        # guest/*.tbd (which list symbol names only), ldid signs it with the entitlement SpringBoard needs.
        wi-relay = pkgs.stdenvNoCC.mkDerivation {
          pname = "ios-safari-mcp-wi-relay";
          version = "0.2.0";
          src = ./guest;
          nativeBuildInputs = [ pkgs.llvmPackages.clang-unwrapped pkgs.lld pkgs.ldid-procursus ];
          dontConfigure = true;
          buildPhase = ''
            clang --target=arm64-apple-ios16.5 -nostdlibinc -fblocks -O1 -Wall -Werror -c wi_relay.c -o wi_relay.o
            ld64.lld -arch arm64 -platform_version ios 16.5 16.5 -o wi_relay wi_relay.o libSystem.tbd CoreFoundation.tbd -e _main
            ldid -Swi_relay.entitlements wi_relay
          '';
          installPhase = "install -Dm755 wi_relay $out/libexec/wi_relay";
        };

        ios-safari-mcp = pkgs.python3Packages.buildPythonApplication {
          pname = "ios-safari-mcp";
          version = "0.2.0";
          pyproject = true;
          src = ./.;
          build-system = [ pkgs.python3Packages.setuptools ];
          nativeCheckInputs = [ pkgs.python3Packages.pytest ];
          checkPhase = "pytest tests";
          # the scripts and the guest sources travel with the package; the relay is the one built above
          postInstall = ''
            share=$out/share/ios-safari-mcp
            mkdir -p $share
            cp -r scripts guest $share/
            install -m755 ${wi-relay}/libexec/wi_relay $share/guest/wi_relay
          '';
          makeWrapperArgs = [
            "--set" "IOS_SAFARI_MCP_ROOT" "${placeholder "out"}/share/ios-safari-mcp"
            "--set" "IOS_SAFARI_MCP_PREBUILT_RELAY" "1"
            "--set" "IOS_SAFARI_MCP_NIXPKGS" "${nixpkgs}"
            "--prefix" "PATH" ":" (pkgs.lib.makeBinPath [ pkgs.nix pkgs.gzip pkgs.coreutils pkgs.bash pkgs.gnugrep ])
          ];
          meta = {
            description = "MCP server for iOS 16.5 Safari in QEMU on the HackiOS kernel";
            license = pkgs.lib.licenses.mit;
            mainProgram = "ios-safari-mcp";
            platforms = systems;
          };
        };

        default = ios-safari-mcp;
      });

      apps = forAll (pkgs: {
        default = { type = "app"; program = "${self.packages.${pkgs.stdenv.hostPlatform.system}.default}/bin/ios-safari-mcp"; };
      });

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ pkgs.python3 pkgs.python3Packages.pytest pkgs.ruff pkgs.llvmPackages.clang-unwrapped pkgs.lld pkgs.ldid-procursus ];
          shellHook = ''
            export PYTHONPATH=$PWD/src''${PYTHONPATH:+:$PYTHONPATH}
            echo "ios-safari-mcp dev shell: pytest tests | ruff check . | bin/ios-safari-mcp doctor"
          '';
        };
      });

      checks = forAll (pkgs: {
        package = self.packages.${pkgs.stdenv.hostPlatform.system}.ios-safari-mcp;            # builds it and runs the tests
        wi-relay = self.packages.${pkgs.stdenv.hostPlatform.system}.wi-relay;
        lint = pkgs.runCommand "ios-safari-mcp-lint" { nativeBuildInputs = [ pkgs.ruff ]; } ''
          cd ${./.}
          ruff check --no-cache .
          touch $out
        '';
      });
    };
}
