# Third-Party Notices

本项目整合了以下开源组件。各组件版权归其原作者所有，许可证条款见各自仓库。

| 组件 | 仓库 | 许可证 | 在本项目中的角色 |
|---|---|---|---|
| OpenHer | https://github.com/kellyvv/OpenHer | Apache-2.0 | 后端人格引擎（Genome / Drives / Metabolism / Style Memory），计划以 `vendor/OpenHer` 引入 |
| Distilly（原 同事.skill / colleague-skill） | https://github.com/titanwings/distilly | MIT | 前端聊天记录蒸馏流程与 Persona 结构，计划以 `vendor/distilly` 引入 |
| EverOS（原 EverMemOS） | https://github.com/EverMind-AI/EverOS | Apache-2.0 | 可选长期记忆后端（默认关闭，走本地 SQLite） |

## 许可证兼容性说明

- 本仓库采用 **Apache-2.0**：可同时覆盖 MIT（Distilly）与 Apache-2.0（OpenHer / EverOS）代码；
  反向（用 MIT 承载 Apache-2.0 代码）不成立，因为 Apache-2.0 附带 NOTICE 与专利条款不可剥离。
- MIT 组件保留其版权声明即可；Apache-2.0 组件须保留 LICENSE、NOTICE 及变更声明。
- 上游代码引入时**不修改其 LICENSE / NOTICE 文件**。
