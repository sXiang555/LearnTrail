package com.learntrail.search.service;

import com.learntrail.common.domain.dto.PageDTO;
import org.elasticsearch.ElasticsearchStatusException;
import org.elasticsearch.action.index.IndexRequest;
import org.elasticsearch.action.search.SearchRequest;
import org.elasticsearch.client.RequestOptions;
import org.elasticsearch.client.RestHighLevelClient;
import org.elasticsearch.client.indices.CreateIndexRequest;
import org.elasticsearch.client.indices.GetIndexRequest;
import org.elasticsearch.index.VersionType;
import org.elasticsearch.index.query.QueryBuilders;
import org.elasticsearch.rest.RestStatus;
import org.elasticsearch.search.SearchHit;
import org.elasticsearch.search.builder.SearchSourceBuilder;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@Service
@ConditionalOnProperty(name = "learntrail.search.content.enabled", havingValue = "true")
public class PublicContentIndex {

    private final RestHighLevelClient elasticsearch;
    private final String index;
    private volatile boolean initialized;

    public PublicContentIndex(RestHighLevelClient elasticsearch,
                              @Value("${learntrail.search.content.index:learntrail-public-content-v1}") String index) {
        this.elasticsearch = elasticsearch;
        this.index = index;
    }

    private synchronized void initialize() throws IOException {
        if (initialized) {
            return;
        }
        if (!elasticsearch.indices().exists(new GetIndexRequest(index), RequestOptions.DEFAULT)) {
            CreateIndexRequest create = new CreateIndexRequest(index);
            create.settings(Map.of("number_of_shards", 1, "number_of_replicas", 0));
            Map<String, String> text = Map.of("type", "text", "analyzer", "ik_max_word",
                    "search_analyzer", "ik_smart");
            create.mapping(Map.of("dynamic", "strict", "properties", Map.of(
                    "kind", Map.of("type", "keyword"), "id", Map.of("type", "keyword"),
                    "slug", Map.of("type", "keyword"), "visible", Map.of("type", "boolean"),
                    "title", text, "summary", text, "body", text)));
            try {
                elasticsearch.indices().create(create, RequestOptions.DEFAULT);
            } catch (ElasticsearchStatusException e) {
                if (!elasticsearch.indices().exists(new GetIndexRequest(index), RequestOptions.DEFAULT)) {
                    throw e;
                }
            }
        }
        initialized = true;
    }

    public void write(String kind, String id, long revision, boolean visible,
                      String slug, String title, String summary, String body) throws IOException {
        if (!initialized) {
            initialize();
        }
        IndexRequest request = new IndexRequest(index).id(kind + ":" + id)
                .version(revision).versionType(VersionType.EXTERNAL)
                .source(Map.of("kind", kind, "id", id, "visible", visible,
                        "slug", visible ? slug : "", "title", visible ? title : "",
                        "summary", visible ? summary : "", "body", visible ? body : ""));
        try {
            elasticsearch.index(request, RequestOptions.DEFAULT);
        } catch (ElasticsearchStatusException e) {
            if (e.status() != RestStatus.CONFLICT) {
                throw e;
            }
        }
    }

    public PageDTO<Result> search(String keyword, String kind, int page, int size) throws IOException {
        if (!initialized) {
            initialize();
        }
        var query = QueryBuilders.boolQuery()
                .filter(QueryBuilders.termQuery("visible", true))
                .must(QueryBuilders.multiMatchQuery(keyword)
                        .field("title", 4f).field("summary", 2f).field("body"));
        if (!"all".equals(kind)) {
            query.filter(QueryBuilders.termQuery("kind", kind));
        }
        SearchSourceBuilder source = new SearchSourceBuilder().query(query)
                .from((page - 1) * size).size(size).trackTotalHits(true)
                .sort("_score", org.elasticsearch.search.sort.SortOrder.DESC)
                .sort("kind").sort("id")
                .fetchSource(new String[]{"kind", "id", "slug", "title", "summary"}, null);
        var hits = elasticsearch.search(new SearchRequest(index).source(source), RequestOptions.DEFAULT).getHits();
        List<Result> results = new ArrayList<>();
        for (SearchHit hit : hits) {
            Map<String, Object> fields = hit.getSourceAsMap();
            results.add(new Result((String) fields.get("kind"), (String) fields.get("id"),
                    (String) fields.get("slug"), (String) fields.get("title"), (String) fields.get("summary")));
        }
        long total = hits.getTotalHits().value;
        return new PageDTO<>(total, (total + size - 1) / size, results);
    }

    @lombok.Value
    public static class Result {
        String kind;
        String id;
        String slug;
        String title;
        String summary;
    }
}
