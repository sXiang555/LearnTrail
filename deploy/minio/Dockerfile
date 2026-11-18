FROM scratch
LABEL org.opencontainers.image.source="https://github.com/minio/minio" \
      org.opencontainers.image.version="RELEASE.2025-10-15T17-29-55Z" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
COPY minio /minio
ENV HOME=/tmp
USER 10001:10001
EXPOSE 9000 9001
ENTRYPOINT ["/minio"]
CMD ["--config-dir", "/tmp/minio-config", "server", "/data", "--console-address", ":9001"]
