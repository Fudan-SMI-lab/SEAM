# Changelog

## [v1.2.1] - 2026-08-20
### Added
- 新增TUI dashboard，提供更简洁的迁移进度显示。可以按q退出TUI。
- 新增续做功能`--continue-from`参数。
- 新增E2E判断严格模式review gate参数`--review`,`--no-review` 。
- 新增迁移迭代自动重试尝试次数控制的`--max-iter`、`--max-review-iter`参数。
- 完成120+模型适配，340+份适配报告公开在[SEAM网站](https://alidocs.dingtalk.com/i/nodes/P0MALyR8klYkMnpXHYRNKrzwW3bzYmDO)。

### Changed
- 运行时log优化。
- 调整用户接口和文档，使更加易用。部分文档迁移到SEAM网站的文档进行更新，本Git仓库会简化文档。
- 仓库重构，优化代码结构，提高可维护性。

### Performance
- 优化执行逻辑，整体运行性能提升。
- python版本兼容优化，增加适配了3.10及3.10以下版本的python。
- 345份迁移报告确认89.85%的任务能在30分钟内完成。


## [v1.1.0] - 2026-05-27
### Added
- SEAM新增支持2款GPU的迁移适配。
- 异构GPU环境自识别，和自动选配YAML文件运行。
- 支持基于用户当前镜像环境、或自动选择用户本地镜像创建容器，进行增量适配，提升迁移效率。

### Changed
- 调整用户接口和文档，使更加易用。
- 仓库重构，优化代码结构，提高可维护性。

### Performance
- 优化执行逻辑，整体运行性能提升。


## [v1.0.0] - 2026-04-30
### Added
- SEAM的仓库主体完成，实现3款框架模型在1款GPU上实现自动化迁移。
