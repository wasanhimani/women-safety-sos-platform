# Build from the repository root:  docker build -f services/web/Dockerfile .
# "unprivileged" nginx runs as a normal user and listens on 8080.
FROM nginxinc/nginx-unprivileged:stable-alpine
COPY services/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY services/web/public /usr/share/nginx/html
EXPOSE 8080
