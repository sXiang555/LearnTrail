"""Small versioned Markdown skill catalog; files never become executable code."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from learntrail_agent.errors import AppError


class SkillMetadata(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    name: str=Field(pattern=r"^[a-z][a-z0-9-]{1,48}$")
    description: str=Field(min_length=5,max_length=300)
    scenarios: list[str]=Field(min_length=1,max_length=8)
    triggers: list[str]=Field(min_length=1,max_length=12)
    tools: list[str]=Field(min_length=1,max_length=16)
    optional_tools: list[str]=Field(default_factory=list,max_length=16)


class UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    result={}
    for key_node,value_node in node.value:
        key=loader.construct_object(key_node)
        if not isinstance(key,str) or key in result:raise ValueError("Duplicate or invalid skill metadata key")
        result[key]=loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,_mapping)


@dataclass(frozen=True)
class Skill:
    metadata: SkillMetadata
    path: Path
    version: str

    def public(self) -> dict:
        return {**self.metadata.model_dump(),"version":self.version}


class SkillCatalog:
    def __init__(self, root: Path) -> None:
        self.root=root.resolve()

    def _read(self, path: Path) -> tuple[bytes,str,str]:
        if not path.resolve().is_relative_to(self.root) or path.is_symlink():
            raise ValueError("Skill path must remain inside the trusted directory")
        raw=path.read_bytes()
        if len(raw)>16000:raise ValueError("Skill file exceeds size limit")
        text=raw.decode("utf-8")
        parts=text.split("---",2)
        if len(parts)!=3 or parts[0].strip() or len(parts[1])>4000:
            raise ValueError("Skill requires bounded YAML front matter")
        return raw,parts[1],parts[2].strip()

    def discover(self) -> dict[str,Skill]:
        paths=sorted(self.root.glob("*/SKILL.md"))
        if len(paths)>16:raise AppError("SKILL_INVALID","技能数量超出配置限制。",503)
        result={}
        try:
            for path in paths:
                raw,header,_=self._read(path)
                metadata=SkillMetadata.model_validate(yaml.load(header,Loader=UniqueLoader))
                if metadata.name!=path.parent.name or metadata.name in result or metadata.name=="auto":
                    raise ValueError("Skill name must match its unique directory")
                if any(not isinstance(s,str) or not 1<=len(s)<=120 for s in metadata.triggers+metadata.scenarios):
                    raise ValueError("Unbounded skill metadata")
                result[metadata.name]=Skill(metadata,path,hashlib.sha256(raw).hexdigest())
        except (OSError,ValueError,yaml.YAMLError,ValidationError):
            raise AppError("SKILL_INVALID","技能文件或元数据不合法。",503) from None
        return result

    def choose(self, question: str, requested: str | None = None) -> Skill | None:
        catalog=self.discover()
        if requested and requested!="auto":
            if requested not in catalog:raise AppError("SKILL_NOT_FOUND","技能不存在。",404)
            return catalog[requested]
        scored=[(sum(len(t) for t in skill.metadata.triggers if t.casefold() in question.casefold()),skill)
                for skill in catalog.values()]
        scored.sort(key=lambda item:item[0],reverse=True)
        return scored[0][1] if scored and scored[0][0]>0 else None

    def load(self, skill: Skill, available: frozenset[str]) -> dict:
        if not set(skill.metadata.tools)<=available:
            raise AppError("SKILL_TOOL_UNAVAILABLE","此技能所需工具未获服务端授权。",503)
        raw,_,body=self._read(skill.path)
        if hashlib.sha256(raw).hexdigest()!=skill.version:
            raise AppError("SKILL_CHANGED","技能版本已变化，请重新发起任务。",409)
        if len(body)>6000:raise AppError("SKILL_INVALID","技能正文过长。",503)
        permitted=sorted(set(skill.metadata.tools)|(set(skill.metadata.optional_tools)&available))
        return {"name":skill.metadata.name,"description":skill.metadata.description,"version":skill.version,
                "tools":permitted,"guide":body}

    def restore(self, saved: dict, available: frozenset[str]) -> dict:
        current=self.discover().get(saved["name"])
        if not current or current.version!=saved["version"]:
            raise AppError("SKILL_CHANGED","技能已修改或移除，不能恢复旧执行策略。",409)
        loaded=self.load(current,available)
        if loaded["tools"]!=saved["tools"]:
            raise AppError("SKILL_CHANGED","技能工具授权已变化。",409)
        return loaded
