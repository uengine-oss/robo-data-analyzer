FROM ubuntu:22.04

WORKDIR /app
COPY src/ /app/src/
COPY CMakeLists.txt /app/

RUN apt-get update && apt-get install -y \
    cmake \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

ENV APP_ENV=production
EXPOSE 8080

CMD ["/app/build/mini_project"]
