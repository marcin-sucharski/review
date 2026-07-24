{
  description = "Fast terminal code review for local Git changes";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  };

  outputs =
    { self, nixpkgs }:
    let
      lib = nixpkgs.lib;
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = lib.genAttrs systems;
      pkgsFor = system: import nixpkgs { inherit system; };
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
          runtimePath = lib.makeBinPath [
            pkgs.gitMinimal
            pkgs.tmux
          ];
        in
        rec {
          review = pkgs.rustPlatform.buildRustPackage {
            pname = "review";
            version = "0.2.2";
            src = lib.fileset.toSource {
              root = ./.;
              fileset = lib.fileset.unions [
                ./Cargo.lock
                ./Cargo.toml
                ./README.md
                ./src
                ./tests
              ];
            };

            cargoLock.lockFile = ./Cargo.lock;

            nativeBuildInputs = [
              pkgs.makeWrapper
            ];

            nativeCheckInputs = [
              pkgs.gitMinimal
              pkgs.tmux
            ];

            checkPhase = ''
              runHook preCheck
              cargo test --all-targets
              runHook postCheck
            '';

            postInstall = ''
              wrapProgram "$out/bin/review" \
                --prefix PATH : ${runtimePath}
            '';

            meta = {
              description = "Fast terminal code review for local Git changes";
              mainProgram = "review";
              platforms = lib.platforms.linux;
            };
          };

          default = review;
        }
      );

      apps = forAllSystems (
        system:
        rec {
          review = {
            type = "app";
            program = lib.getExe self.packages.${system}.review;
            meta.description = "Fast terminal code review for local Git changes";
          };

          default = review;
        }
      );

      checks = forAllSystems (system: {
        review = self.packages.${system}.review;
      });

      devShells = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
        in
        {
          default = pkgs.mkShell {
            packages = [
              pkgs.cargo
              pkgs.clippy
              pkgs.gitMinimal
              pkgs.rustc
              pkgs.rustfmt
              pkgs.tmux
            ];
          };
        }
      );
    };
}
