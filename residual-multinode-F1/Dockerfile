FROM python:3.12-alpine
WORKDIR /app
COPY app/ /app/
RUN addgroup -S demo && adduser -S -G demo -u 10001 demo \
    && chmod 0555 /app/controller.py /app/agent.py
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["python3"]
CMD ["/app/controller.py"]
