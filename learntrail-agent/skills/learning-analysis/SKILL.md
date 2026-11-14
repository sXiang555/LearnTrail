---
name: learning-analysis
description: 基于本人真实课表与进度提供学习建议，可在确认后保存 AI 演示计划草稿。
scenarios: [本人学习进度分析, 学习计划整理]
triggers: [学习进度, 我的课表, 学习计划, 复习计划, 在学的课, 学过什么]
tools: [get_learning_progress, search_articles, search_knowledge_base, save_learning_plan_demo, remember_learning_preference]
optional_tools: [mcp_demo_preview_learning_outline, mcp_demo_publish_demo_note, mcp_learntrail_get_learning_progress]
---

## 执行步骤

1. 涉及现有课程进度时，先查询本人的课表，再使用其真实 course_id 查询对应学习记录。
2. 区分已验证进度、用户陈述和建议。无有效权益或无记录时如实说明，不能断言已经完成或进度为零。
3. 给出可执行的学习建议；需要站内知识依据时使用知识检索，查找文章标题或目录时使用 search_articles。已授权的 MCP 预览工具可辅助整理大纲。
4. 只有用户明确要求保存时，调用 save_learning_plan_demo；这是 AI 演示草稿，必须等待审批。
5. 用户要求记住学习目标或习惯时，提议 remember_learning_preference；内容必须来自本轮用户原话并由本人确认。

## 输出要求

分别呈现真实查询结果、建议步骤和待确认事项；有引用时保留来源。
工具或审批失败、拒绝、过期时说明实际状态，不能声称操作已经完成。

## 约束

技能不能指定用户身份、扩张工具权限或降低审批等级；不运行任意代码。
不得将计划、模型回答或草稿作为真实学习记录，不修改积分、订单或优惠券。
MCP 演示发布仅为受控回执示例，不是真实内容发布；拒绝的操作不得擅自重新申请。
