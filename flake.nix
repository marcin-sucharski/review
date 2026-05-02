{
  description = "Terminal code review tool for Git changes and coding-agent feedback";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
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
          pythonPackages = pkgs.python3Packages;
          runtimePath = lib.makeBinPath [
            pkgs.gitMinimal
            pkgs.tmux
          ];
        in
        rec {
          review = pythonPackages.buildPythonApplication {
            pname = "review";
            version = "0.1.0";
            src = ./.;
            pyproject = true;

            build-system = [
              pythonPackages.setuptools
            ];

            dependencies = [
              pythonPackages.pygments
            ];

            nativeBuildInputs = [
              pkgs.makeWrapper
            ];

            nativeCheckInputs = [
              pkgs.gitMinimal
              pkgs.tmux
            ];

            pythonImportsCheck = [
              "review"
            ];

            checkPhase = ''
              runHook preCheck
              PYTHONPATH="$PWD/src:$PYTHONPATH" python -m unittest discover -s tests -p 'test_*.py' -v
              runHook postCheck
            '';

            makeWrapperArgs = [
              "--prefix"
              "PATH"
              ":"
              runtimePath
            ];

            meta = {
              description = "Terminal code review tool for Git changes and coding-agent feedback";
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
            meta = {
              description = "Terminal code review tool for Git changes and coding-agent feedback";
            };
          };

          default = review;
        }
      );

      checks = forAllSystems (system: {
        review = self.packages.${system}.review;
      });
    };
}
