"""Task routing and instructions for the LearnTrail assistants."""

from typing import Literal

Assistant = Literal["auto", "learning", "creator", "operations", "collaborate"]
Specialist = Literal["learning", "creator", "operations"]

INSTRUCTIONS = {
    "learning": "你是 LearnTrail 学习助手。需要真实知识、文章或学习进度时使用注册工具。",
    "creator": (
        "你是 LearnTrail 创作者助手。根据用户提供的素材撰写大纲、润色稿件、规划视频专栏或做内容质量检查。"
        "按需查询已发布资料并标明引用。需要用户稿件而用户尚未提供时，请其粘贴相关段落。"
    ),
    "operations": (
        "你是 LearnTrail 运营助手。对已发布文章做内容盘点、主题对比、阅读量或点赞量排行和选题建议。"
        "遇到真实数据问题先查文章工具，区分查询时的公开计数、分页样本与全站数据。"
        "内容风险提示应说明依据和复核建议，勿将推断写成已确认事实。"
    ),
}

PUBLIC_ARTICLE_TOOLS = frozenset({"search_articles", "search_knowledge_base"})

CREATOR_INTENTS = ("创作者", "创作", "润色", "大纲", "拟标题", "写一篇", "写一份", "脚本", "改稿", "稿件", "分集", "视频专栏")
OPERATIONS_INTENTS = ("运营", "盘点", "排行榜", "排名", "阅读量", "浏览量", "点赞量", "内容风险", "内容覆盖")


def delegate(assistant: Assistant, question: str) -> tuple[Specialist, ...]:
    if assistant == "collaborate":
        return ("operations", "creator")
    if assistant != "auto":
        return (assistant,)
    creator = any(intent in question for intent in CREATOR_INTENTS)
    operations = any(intent in question for intent in OPERATIONS_INTENTS)
    if creator and operations:
        return ("operations", "creator")
    if operations:
        return ("operations",)
    if creator:
        return ("creator",)
    return ("learning",)
