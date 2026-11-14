package com.learntrail.api.client.course;

import com.learntrail.api.dto.course.CategoryBasicDTO;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;

import java.util.List;

@FeignClient(contextId = "category",value = "lt-course",path = "categorys")
public interface CategoryClient {

    /**
     * 获取所有课程及课程分类
     * @return  所有课程及课程分类
     */
    @GetMapping("getAllOfOneLevel")
    List<CategoryBasicDTO> getAllOfOneLevel();
}
