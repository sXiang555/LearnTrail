"""Read the signed user's existing Java lessons/progress; never submit records."""

import httpx
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from pydantic import BaseModel, Field, StrictBool, StrictInt, ValidationError, field_serializer, field_validator

from learntrail_agent.clients.java import JavaPage, PublishedArticle, read_user_data
from learntrail_agent.errors import AppError
from learntrail_agent.settings import Settings
from learntrail_agent.observability import observed


class LessonSummary(BaseModel):
    course_id: int = Field(alias="courseId", gt=0, le=9223372036854775807)
    course_name: str = Field(alias="courseName", min_length=1, max_length=500)
    sections: StrictInt | None = Field(default=None, ge=0)
    status: StrictInt | None = Field(default=None, ge=0, le=3)
    learned_sections: StrictInt | None = Field(default=None, alias="learnedSections", ge=0)

    @field_validator("course_id", mode="before")
    @classmethod
    def java_id(cls, value: object) -> int:
        return PublishedArticle.java_long(value)

    @field_serializer("course_id")
    def serialized_id(self, value: int) -> str:
        return str(value)


class LessonPage(JavaPage):
    items: list[LessonSummary] = Field(alias="list", max_length=30)


class SectionProgress(BaseModel):
    section_id: int = Field(alias="sectionId", gt=0, le=9223372036854775807)
    moment: StrictInt | None = Field(default=None, ge=0)
    finished: StrictBool | None = None

    @field_validator("section_id", mode="before")
    @classmethod
    def java_id(cls, value: object) -> int:
        return PublishedArticle.java_long(value)

    @field_serializer("section_id")
    def serialized_id(self, value: int) -> str:
        return str(value)


class CourseProgress(BaseModel):
    latest_section_id: int | None = Field(default=None, alias="latestSectionId", gt=0, le=9223372036854775807)
    records: list[SectionProgress] = Field(max_length=20000)

    @field_validator("latest_section_id", mode="before")
    @classmethod
    def java_id(cls, value: object) -> int | None:
        return None if value is None else PublishedArticle.java_long(value)

    @field_serializer("latest_section_id")
    def serialized_id(self, value: int | None) -> str | None:
        return str(value) if value is not None else None


class JavaLearningClient:
    def __init__(self, http: httpx.AsyncClient, settings: Settings, key: RSAPublicKey | None) -> None:
        self.http, self.settings, self.key = http, settings, key

    @observed("java.learning_page")
    async def lessons(self, token: str, user_id: int, page_no: int, page_size: int) -> LessonPage:
        if not 1 <= page_no <= 100000 or not 1 <= page_size <= 30:
            raise AppError("INVALID_TOOL_ARGUMENTS", "课表分页参数超出范围。", 422)
        data = await read_user_data(self.http,self.settings,self.key,token,user_id,"/ls/lessons/page",
                                    {"pageNo":page_no,"pageSize":page_size})
        try:
            return LessonPage.model_validate(data)
        except ValidationError:
            raise AppError("JAVA_READ_INVALID_RESPONSE", "课表响应格式异常。", 502) from None

    @observed("java.learning_progress")
    async def progress(self, token: str, user_id: int, course_id: int) -> CourseProgress | None:
        if type(course_id) is not int or not 0 < course_id <= 9223372036854775807:
            raise AppError("INVALID_TOOL_ARGUMENTS", "课程标识不合法。", 422)
        data = await read_user_data(self.http,self.settings,self.key,token,user_id,
                                    "/ls/learning-records/course/"+str(course_id))
        if data is None:
            return None  # Java found no current user's valid entitlement; not zero progress.
        try:
            result = CourseProgress.model_validate(data)
            return result.model_copy(update={"records":sorted(result.records,key=lambda row:row.section_id)})
        except ValidationError:
            raise AppError("JAVA_READ_INVALID_RESPONSE", "学习进度响应格式异常。", 502) from None
