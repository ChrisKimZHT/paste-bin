FROM golang:1.27-alpine AS build

WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download
COPY main.go server.go store.go index.html ./
ARG VERSION=dev
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w -X main.version=${VERSION}" -o /pastebin .

FROM scratch
COPY --from=build /pastebin /pastebin

ENV PASTEBIN_HOST=0.0.0.0 \
    PASTEBIN_PORT=8000 \
    PASTEBIN_MAX_BYTES=131072 \
    PASTEBIN_MAX_ENTRIES=1024

EXPOSE 8000

CMD ["/pastebin"]
