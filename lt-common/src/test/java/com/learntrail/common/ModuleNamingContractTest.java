package com.learntrail.common;

import org.junit.jupiter.api.Test;
import org.springframework.boot.env.YamlPropertySourceLoader;
import org.springframework.core.env.PropertySource;
import org.springframework.core.io.FileSystemResource;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Set;
import java.util.HashSet;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Collectors;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.*;

class ModuleNamingContractTest {
    private Path root() {
        Path path = Path.of("").toAbsolutePath();
        while (path != null && !Files.isRegularFile(path.resolve(".github/workflows/verify.yml"))) {
            path = path.getParent();
        }
        assertNotNull(path, "Repository root must be available");
        return path;
    }

    private List<Path> sources() throws Exception {
        Path root = root();
        try (Stream<Path> paths = Files.walk(root)) {
            return paths.filter(Files::isRegularFile)
                    .filter(path -> root.relativize(path).getName(0).toString().startsWith("lt-"))
                    .filter(path -> path.toString().contains("/src/main/"))
                    .collect(Collectors.toList());
        }
    }

    @Test
    void allServiceYamlLoadsWithTheSameStrictLoaderAsSpringBoot() throws Exception {
        YamlPropertySourceLoader loader = new YamlPropertySourceLoader();
        int count = 0;
        for (Path path : sources()) {
            if (path.toString().endsWith(".yml") || path.toString().endsWith(".yaml")) {
                assertDoesNotThrow(() -> loader.load(path.toString(), new FileSystemResource(path)), path.toString());
                count++;
            }
        }
        assertTrue(count >= 42);
    }

    @Test
    void everyExecutableServiceUsesItsModuleIdentity() throws Exception {
        assertEquals(14, serviceNames().size());
    }

    private Set<String> serviceNames() throws Exception {
        Set<String> names = new HashSet<>();
        for (Path path : sources()) {
            if (!path.getFileName().toString().equals("bootstrap.yml")) continue;
            List<PropertySource<?>> yaml = new YamlPropertySourceLoader()
                    .load(path.toString(), new FileSystemResource(path));
            String expected = root().relativize(path).getName(0).toString();
            Object actual = yaml.get(0).getProperty("spring.application.name");
            assertEquals(expected, actual, path.toString());
            assertTrue(names.add(expected), "Duplicate service registration: " + expected);
        }
        return names;
    }

    @Test
    void gatewayFeignAndPublicKeyDiscoveryResolveDeclaredServices() throws Exception {
        Set<String> names = serviceNames();
        String gateway = Files.readString(root().resolve("lt-gateway/src/main/resources/bootstrap.yml"));
        Matcher routes = Pattern.compile("lb://(lt-[a-z]+)").matcher(gateway);
        int count = 0;
        while (routes.find()) {
            assertTrue(names.contains(routes.group(1)), "Missing route service: " + routes.group(1));
            count++;
        }
        assertEquals(14, count);
        Pattern client = Pattern.compile("@FeignClient\\(([^)]*)\\)", Pattern.DOTALL);
        for (Path path : sources()) {
            if (!path.toString().endsWith(".java")) continue;
            Matcher annotations = client.matcher(Files.readString(path));
            while (annotations.find()) {
                Matcher target = Pattern.compile("\"(lt-[a-z]+)\"").matcher(annotations.group(1));
                assertTrue(target.find(), "Feign service name must be explicit: " + path);
                assertTrue(names.contains(target.group(1)), "Missing Feign service: " + path);
            }
        }
        String discovery = Files.readString(root().resolve("lt-auth/lt-auth-gateway-sdk/src/main/java/"
                + "com/learntrail/authsdk/gateway/util/JwtSignerHolder.java"));
        assertTrue(discovery.contains("getInstances(\"lt-auth\")"));
    }
}
