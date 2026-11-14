ARG JAVA_BASE_IMAGE=eclipse-temurin:11-jre
FROM ${JAVA_BASE_IMAGE}

LABEL org.opencontainers.image.title="LearnTrail" \
      org.opencontainers.image.description="Learning, content and commerce service"

ENV TZ=Asia/Shanghai \
    JAVA_OPTS=""

WORKDIR /app
ARG JAR_FILE=app.jar
COPY ${JAR_FILE} /app/app.jar

# JVM options must precede -jar; exec forwards termination signals to Java.
ENTRYPOINT ["sh", "-c", "exec java $JAVA_OPTS -jar /app/app.jar \"$@\"", "--"]
