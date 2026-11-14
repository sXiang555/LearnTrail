package com.learntrail.common.autoconfigure.mq;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.learntrail.common.domain.dto.LoginUserDTO;
import org.junit.jupiter.api.Test;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageProperties;
import org.springframework.amqp.support.converter.MessageConverter;
import org.springframework.mock.env.MockEnvironment;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

class MqNamingCompatibilityTest {
    private final MqConfig config = new MqConfig();
    private final MessageConverter converter = config.messageConverter(new ObjectMapper());
    private static final String LEGACY_DTO = "com.tianji.common.domain.dto.LoginUserDTO";

    private Message message(String json, String type, String contentType, String keyType) {
        MessageProperties properties = new MessageProperties();
        properties.setContentType(MessageProperties.CONTENT_TYPE_JSON);
        properties.setHeader("__TypeId__", type);
        if (contentType != null) properties.setHeader("__ContentTypeId__", contentType);
        if (keyType != null) properties.setHeader("__KeyTypeId__", keyType);
        return new Message(json.getBytes(StandardCharsets.UTF_8), properties);
    }

    @Test
    void consumesLegacyDtoWithoutChangingOriginalHeaders() {
        Message message = message("{\"userId\":42}", LEGACY_DTO, null, null);
        LoginUserDTO user = (LoginUserDTO) converter.fromMessage(message);
        assertEquals(42L, user.getUserId());
        assertEquals(LEGACY_DTO, message.getMessageProperties().getHeader("__TypeId__"));
    }

    @Test
    void consumesLegacyCollectionContentType() {
        List<?> users = (List<?>) converter.fromMessage(message(
                "[{\"userId\":43}]", "java.util.ArrayList", LEGACY_DTO, null));
        assertEquals(43L, ((LoginUserDTO) users.get(0)).getUserId());
    }

    @Test
    void consumesLegacyArrayType() {
        LoginUserDTO[] users = (LoginUserDTO[]) converter.fromMessage(message(
                "[{\"userId\":44}]", "[L" + LEGACY_DTO + ";", null, null));
        assertEquals(44L, users[0].getUserId());
    }

    @Test
    void consumesMapWithLegacyValueType() {
        Map<?, ?> users = (Map<?, ?>) converter.fromMessage(message(
                "{\"owner\":{\"userId\":45}}", "java.util.HashMap", LEGACY_DTO, "java.lang.String"));
        assertEquals(45L, ((LoginUserDTO) users.get("owner")).getUserId());
    }

    @Test
    void newMessagesRoundTripWithLearnTrailType() {
        LoginUserDTO user = new LoginUserDTO();
        user.setUserId(46L);
        Message message = converter.toMessage(user, new MessageProperties());
        assertEquals(LoginUserDTO.class.getName(), message.getMessageProperties().getHeader("__TypeId__"));
        assertEquals(46L, ((LoginUserDTO) converter.fromMessage(message)).getUserId());
    }

    @Test
    void discoveryRenameKeepsConfiguredDurableQueueAndBinding() {
        config.setEnvironment(new MockEnvironment().withProperty("spring.application.name", "lt-promotion")
                .withProperty("tj.mq.error-service-name", "promotion-service"));
        assertEquals("error.promotion-service.queue", config.errorQueue().getName());
        assertEquals("error.promotion-service", config.errorBinding(
                config.errorQueue(), config.errorMessageExchange()).getRoutingKey());
    }

    @Test
    void unconfiguredQueueStillUsesApplicationName() {
        config.setEnvironment(new MockEnvironment().withProperty("spring.application.name", "custom-service"));
        assertEquals("error.custom-service.queue", config.errorQueue().getName());
    }
}
