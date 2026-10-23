# FHS environment for the HackiOS scripts: they use #!/bin/bash and need 7-Zip 23+ as `7z`.
let
  pkgs = import <nixpkgs> { };
  alias7z = pkgs.runCommand "7z-alias" { } "mkdir -p $out/bin && ln -s ${pkgs._7zz}/bin/7zz $out/bin/7z";
in
pkgs.buildFHSEnv {
  name = "hfhs";
  targetPkgs =
    p: with p; [
      alias7z
      bash
      coreutils
      gnugrep
      gnused
      gawk
      findutils
      gnutar
      gzip
      xz
      git
      curl
      python3
      _7zz
      lz4
      qemu
      gcc
      gnumake
      file
      which
      util-linux
    ];
  runScript = "bash";
}
