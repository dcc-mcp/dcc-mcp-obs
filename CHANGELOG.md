# Changelog

All notable changes to this project will be documented in this file.

## [1.3.0](https://github.com/dcc-mcp/dcc-mcp-obs/compare/v1.2.0...v1.3.0) (2026-09-05)


### Features

* separate OBS control and websocket ports ([e824827](https://github.com/dcc-mcp/dcc-mcp-obs/commit/e824827386fe0a35bd704e6b21a698ec59ea8048))


### Bug Fixes

* align tests with current release version ([38c4f5a](https://github.com/dcc-mcp/dcc-mcp-obs/commit/38c4f5a1e86bb96e653a3c35ad9c2428787512ff))
* satisfy Python formatting checks ([f8cccf7](https://github.com/dcc-mcp/dcc-mcp-obs/commit/f8cccf7af13bbf025a9de48f6f6c12d8b9871b7d))

## [1.2.0](https://github.com/dcc-mcp/dcc-mcp-obs/compare/v1.1.0...v1.2.0) (2026-09-01)


### Features

* add built-in agent input overlay ([#31](https://github.com/dcc-mcp/dcc-mcp-obs/issues/31)) ([347a4d0](https://github.com/dcc-mcp/dcc-mcp-obs/commit/347a4d0a1d026510e6875ebb882d3c0f72855484))
* add guarded graceful shutdown ([#30](https://github.com/dcc-mcp/dcc-mcp-obs/issues/30)) ([7a2ef61](https://github.com/dcc-mcp/dcc-mcp-obs/commit/7a2ef613d2ee9448bf949a0191b8e6d3e6dabd70))
* add native OBS DCC MCP menu ([#32](https://github.com/dcc-mcp/dcc-mcp-obs/issues/32)) ([4cffb41](https://github.com/dcc-mcp/dcc-mcp-obs/commit/4cffb41ef091eb66c4a776ab3dfcd5c7f6bd227c))
* add parallel game scene recordings ([#38](https://github.com/dcc-mcp/dcc-mcp-obs/issues/38)) ([2e9cefa](https://github.com/dcc-mcp/dcc-mcp-obs/commit/2e9cefa2727370f8d2aa1ae3b0c6796f47ad8838))
* add self-contained OBS sidecar ([#33](https://github.com/dcc-mcp/dcc-mcp-obs/issues/33)) ([f3fea36](https://github.com/dcc-mcp/dcc-mcp-obs/commit/f3fea366648821523ade51911a3caee3fbd433e2))
* add typed source control domains ([f9ae0e6](https://github.com/dcc-mcp/dcc-mcp-obs/commit/f9ae0e6b856ef65d11226f88e2ea0c3a0dbf726b))
* **obs:** add bounded program frame previews ([#24](https://github.com/dcc-mcp/dcc-mcp-obs/issues/24)) ([b4b56cd](https://github.com/dcc-mcp/dcc-mcp-obs/commit/b4b56cd487b1fa094dd2f91bbca77beb9bc7bf0c))
* **obs:** add exact window capture sources ([#19](https://github.com/dcc-mcp/dcc-mcp-obs/issues/19)) ([3160abe](https://github.com/dcc-mcp/dcc-mcp-obs/commit/3160abe236c6435c52cb95a809b25e1b262c57d8))
* **obs:** add typed scene graph controls ([#16](https://github.com/dcc-mcp/dcc-mcp-obs/issues/16)) ([c67f20d](https://github.com/dcc-mcp/dcc-mcp-obs/commit/c67f20dfe3aa511c3a6cf0456b3a65e093a0d122))
* **obs:** expose recording output diagnostics ([#29](https://github.com/dcc-mcp/dcc-mcp-obs/issues/29)) ([7c01308](https://github.com/dcc-mcp/dcc-mcp-obs/commit/7c01308be3e07e1940195dbd85520781c0e9c99f))
* **obs:** recover window capture after host restart ([#26](https://github.com/dcc-mcp/dcc-mcp-obs/issues/26)) ([5515f22](https://github.com/dcc-mcp/dcc-mcp-obs/commit/5515f22156126eb8ac3676e2023763965044ee01))
* **obs:** restore minimized capture windows ([#28](https://github.com/dcc-mcp/dcc-mcp-obs/issues/28)) ([610c50c](https://github.com/dcc-mcp/dcc-mcp-obs/commit/610c50c1229381c9363af4bde386bb3d5762d2da))
* **obs:** support typed window capture methods ([#22](https://github.com/dcc-mcp/dcc-mcp-obs/issues/22)) ([5e179d6](https://github.com/dcc-mcp/dcc-mcp-obs/commit/5e179d621ef92aa95509ee653b6d2200719ca4b8))


### Bug Fixes

* align sidecar vendor request allowlist ([#36](https://github.com/dcc-mcp/dcc-mcp-obs/issues/36)) ([408ff65](https://github.com/dcc-mcp/dcc-mcp-obs/commit/408ff65102019ee6924a4ecce3b8076fdb447664))
* complete real OBS scene graph acceptance ([226e328](https://github.com/dcc-mcp/dcc-mcp-obs/commit/226e32802e6e2c1023c26e38ef95ea3195923898))
* detect duplicate OBS plugin installs ([#35](https://github.com/dcc-mcp/dcc-mcp-obs/issues/35)) ([47a0d0f](https://github.com/dcc-mcp/dcc-mcp-obs/commit/47a0d0f171920c7bbfab1d95485e9d9ff3c1608e))
* **obs:** allow program frame transport ([#25](https://github.com/dcc-mcp/dcc-mcp-obs/issues/25)) ([dc65963](https://github.com/dcc-mcp/dcc-mcp-obs/commit/dc65963e7d98444f0dd3f82794aca61af836373f))
* **obs:** allow unauthenticated websocket sessions ([#20](https://github.com/dcc-mcp/dcc-mcp-obs/issues/20)) ([d727236](https://github.com/dcc-mcp/dcc-mcp-obs/commit/d727236400c26754db69aa4c867ab040233a72f5))
* **obs:** await recording finalization ([#21](https://github.com/dcc-mcp/dcc-mcp-obs/issues/21)) ([8fa2b2e](https://github.com/dcc-mcp/dcc-mcp-obs/commit/8fa2b2ea82078e232eef53b20dc805ebab6882c2))
* **obs:** bundle current scene tool script ([#23](https://github.com/dcc-mcp/dcc-mcp-obs/issues/23)) ([33d97a4](https://github.com/dcc-mcp/dcc-mcp-obs/commit/33d97a4f137a4bb587af9f93be090910a5ae547d))
* **obs:** classify window rebind as mutation ([#27](https://github.com/dcc-mcp/dcc-mcp-obs/issues/27)) ([ecad34e](https://github.com/dcc-mcp/dcc-mcp-obs/commit/ecad34ead9d21d0705cf089233d67d1e1f97236e))
* **obs:** install Windows plugin under ProgramData ([#18](https://github.com/dcc-mcp/dcc-mcp-obs/issues/18)) ([000e863](https://github.com/dcc-mcp/dcc-mcp-obs/commit/000e8638840c3d636da16c8baa999dc3f9b70855))
* publish runtime-compatible output schemas ([#39](https://github.com/dcc-mcp/dcc-mcp-obs/issues/39)) ([65b819d](https://github.com/dcc-mcp/dcc-mcp-obs/commit/65b819d47eee2f48db9efc4063bb90b2eb097ed9))
* reconcile legacy OBS plugin installs ([#34](https://github.com/dcc-mcp/dcc-mcp-obs/issues/34)) ([065d84d](https://github.com/dcc-mcp/dcc-mcp-obs/commit/065d84d9536874b47eefcf47d87ff5ef72bfae1e))
* render agent input overlay ([#37](https://github.com/dcc-mcp/dcc-mcp-obs/issues/37)) ([9c5a53c](https://github.com/dcc-mcp/dcc-mcp-obs/commit/9c5a53c81d74210e7bb509fb9d3056168b330398))

## [1.1.0](https://github.com/dcc-mcp/dcc-mcp-obs/compare/v1.0.0...v1.1.0) (2026-08-29)


### Features

* **obs:** add profile and operator status controls ([#15](https://github.com/dcc-mcp/dcc-mcp-obs/issues/15)) ([c31261c](https://github.com/dcc-mcp/dcc-mcp-obs/commit/c31261c7770a52821d2740325872b4fbbbcd19de))
* **obs:** add typed streaming and output controls ([#13](https://github.com/dcc-mcp/dcc-mcp-obs/issues/13)) ([845ce20](https://github.com/dcc-mcp/dcc-mcp-obs/commit/845ce20e463b7ae3031fa89a871031473ea252c3))

## 1.0.0 (2026-08-28)


### Features

* add native OBS control plugin ([8d208a2](https://github.com/dcc-mcp/dcc-mcp-obs/commit/8d208a205158b73c10a7e62d56bd9278654e0ac2))


### Bug Fixes

* bind installer identity through recovery retirement ([8faa7a1](https://github.com/dcc-mcp/dcc-mcp-obs/commit/8faa7a17eb540df15c0f1ae3652afed4072bd44a))
* close OBS contract and ownership gaps ([d4cd2ef](https://github.com/dcc-mcp/dcc-mcp-obs/commit/d4cd2ef05ff43b6ab412d0cb32f547fa9ed61690))
* connect release creation to artifact publishing ([11ebed6](https://github.com/dcc-mcp/dcc-mcp-obs/commit/11ebed6c2bed4458427c004ad739b54180333491))
* guard POSIX terminal result identity ([9a9f581](https://github.com/dcc-mcp/dcc-mcp-obs/commit/9a9f58184878080ede51f87b117c11dde91275ed))
* harden cross-platform CI contracts ([ec1cb50](https://github.com/dcc-mcp/dcc-mcp-obs/commit/ec1cb506fd7e2c5a9ac357d75053efb773882a1c))
* harden OBS control contracts ([f18c7dc](https://github.com/dcc-mcp/dcc-mcp-obs/commit/f18c7dcf182e1204d6d6360e096a36d51797f52e))
* lease installer retirement and terminal results ([137ff45](https://github.com/dcc-mcp/dcc-mcp-obs/commit/137ff45a2e59f452f7cd5c1f5f67714d4516ba18))
* make POSIX verification contract truthful ([c1188ec](https://github.com/dcc-mcp/dcc-mcp-obs/commit/c1188ec135b1ac8afd54f7ef8c590f83e3d6ca99))
* repair native build contracts ([17f1e0a](https://github.com/dcc-mcp/dcc-mcp-obs/commit/17f1e0aaec39ddbd106e636df91eb9050640076d))
* repair release-please action pin ([f6f2d8b](https://github.com/dcc-mcp/dcc-mcp-obs/commit/f6f2d8b4d80e7785eee3324df00c3a541070f820))

## [Unreleased]

- Native OBS plugin and DCC-MCP control-plane foundation.
