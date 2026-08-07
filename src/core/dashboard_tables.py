"""Display lookup tables for the dashboard (Chinese localization data).

Pure data: the mapping from internal status/role/phase identifiers to the
user-facing Chinese strings shown in the Rich and Textual renderers. Kept
separate from ``dashboard_text`` so the text-building logic and the
localization table evolve independently.
"""

from __future__ import annotations

STATUS_TEXT = {
    "pending": "待执行",
    "running": "运行中",
    "success": "已完成",
    "passed": "已完成",
    "skipped": "已跳过",
    "failed": "失败",
    "failure": "失败",
    "complete": "已结束",
    "dispatched": "已路由",
}
ROLE_TEXT = {
    "workflow_selector": "工作流选择器",
    "main_engineer": "主迁移智能体",
    "dependency_fixer": "依赖修复智能体",
    "code_adapter": "代码适配智能体",
    "operator_fixer": "算子修复智能体",
    "runtime_analyzer": "运行错误分析器",
    "repair_router": "修复路由器",
}
ROLE_ACTION_TEXT = {
    "workflow_selector": "选择迁移工作流",
    "dependency_fixer": "修复依赖和环境问题",
    "code_adapter": "修改项目代码以适配目标平台",
    "operator_fixer": "修复自定义算子或编译问题",
    "runtime_analyzer": "分析运行失败原因",
    "repair_router": "选择下一步修复角色",
}
PHASE_ACTION_TEXT = {
    "phase_0_env_detect": "检测运行环境和平台能力",
    "phase_1_project_analysis": "分析项目结构、依赖和 CUDA 使用点",
    "phase_1_5_constraint_summary": "整理用户约束和迁移要求",
    "phase_2_venv_create": "准备迁移环境和依赖",
    "phase_3_entry_script": "生成迁移验证入口命令",
    "phase_35_static_validate": "检查入口命令是否能有效验证迁移",
    "phase_4_rule_migration": "执行规则化代码迁移",
    "phase_5_validation": "运行验证并自动修复失败",
    "phase_6_report": "生成报告和使用说明",
    "phase_7a_evaluate": "评估可复用迁移经验",
    "phase_7b_refine": "沉淀可复用迁移经验",
}
PHASE_NUMBER_TEXT = {
    "phase_0_env_detect": "0",
    "phase_1_project_analysis": "1",
    "phase_1_5_constraint_summary": "1.5",
    "phase_2_venv_create": "2",
    "phase_3_entry_script": "3",
    "phase_35_static_validate": "3.5",
    "phase_4_rule_migration": "4",
    "phase_5_validation": "5",
    "phase_6_report": "6",
    "phase_7a_evaluate": "7a",
    "phase_7b_refine": "7b",
}
