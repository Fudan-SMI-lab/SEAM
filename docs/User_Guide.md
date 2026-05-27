# 用户指南


## 快速开始

在您要用的中国产GPU服务器、容器环境里，下载和使用SEAM：
```bash
git clone https://github.com/seam-project/seam.git
cd seam
bash src/scripts/run_seam.sh /path/to/your_original_cuda_project \
  --server_type opencode \
  --server_url http://127.0.0.1:5000
```

运行后：
*   是否跑通：终端最后会直接显示 `E2E TEST PASSED` / `E2E PASS` 或失败信息；也可以通过 `./e2e-reports/migration_utils/<时间戳>/summary.json`获取更具体的信息
    
*   迁移的代码库：会默认写入 `./output_projects/<项目名>_<时间戳>/`，或是执行时输入的参数 `--output-dir`。
    
*   迁移报告：会在迁移后的代码库下创建`.migration_reports/`文件夹, 用于查看迁移后项目本身的验收结果、性能、custom-op迁移情况、构建日志等。
    
*   详细运行时log：在迁移后项目的 `.sm-artifacts/` 下；如果运行失败，可以把运行报告和 `.sm-artifacts/` 一起反馈给我们排查。
    
*   .memory .skill 等文件夹会更新，是SEAM的自进化学习的经验记忆和技能素材，非必要勿删。


## 高级自定义使用

可以自定义修改YAML文件，来实现更复杂的迁移任务。

也可以自定义自己的skill等。

更多细节更新中。


## 使用示例

请查阅[使用案例](Use_Cases.md)

更多细节更新中。
